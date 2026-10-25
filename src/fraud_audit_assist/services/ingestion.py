"""Ingestion: store a batch of events, update the Link Graph, re-score every Account it touched, open Cases.

The whole batch is one transaction (ADR-0015):
1. Lock the batch's Accounts, in sorted order.
2. Append the events (idempotent on event id) and upsert their identifier links.
3. Find the Neighbours the new links can affect, and lock them.
4. Re-score each Account: detect over its timeline, stored Alerts and graph hits; append new hits.
5. Open a Case the first time an Alert reaches High.
6. Commit, then queue the opened Cases, so a rolled-back batch never starts an Investigation (ADR-0004).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from fraud_audit_assist.detection.detector import detect
from fraud_audit_assist.detection.replay import ring_hops
from fraud_audit_assist.domain.events import Event
from fraud_audit_assist.domain.packs import LoadedPack
from fraud_audit_assist.domain.rules import RiskBand
from fraud_audit_assist.errors import InvalidInputError
from fraud_audit_assist.graph.links import links_from
from fraud_audit_assist.graph.rules import graph_hits, neighbours
from fraud_audit_assist.observability.logging import get_logger
from fraud_audit_assist.observability.metrics import Metrics
from fraud_audit_assist.observability.tracing import tracer
from fraud_audit_assist.persistence.db import Database
from fraud_audit_assist.persistence.repositories import (
    AlertRepository,
    CaseRepository,
    ConfirmedFraudRepository,
    EventRepository,
    LinkRepository,
)
from fraud_audit_assist.services.case_queue import CaseQueue

log = get_logger(__name__)

MAX_BATCH = 1000


@dataclass
class IngestResult:
    ingested: list[uuid.UUID] = field(default_factory=list)
    duplicates: list[uuid.UUID] = field(default_factory=list)
    rescored_accounts: list[str] = field(default_factory=list)
    opened_cases: list[uuid.UUID] = field(default_factory=list)


class IngestionService:
    def __init__(
        self,
        db: Database,
        pack: LoadedPack,
        queue: CaseQueue,
        metrics: Metrics,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._db = db
        self._pack = pack
        self._queue = queue
        self._metrics = metrics
        self._clock = clock

    async def ingest(self, events: Sequence[Event]) -> IngestResult:
        self._validate(events)
        started = time.perf_counter()
        with tracer().start_as_current_span("ingest") as span:
            span.set_attribute("faa.batch_size", len(events))

            async def work(session: AsyncSession) -> IngestResult:
                # Each attempt starts from a fresh result, so a replayed transaction never queues a Case twice.
                return await self._ingest_in(session, events)

            result = await self._db.run_transaction(work)
            for case_id in result.opened_cases:
                await self._queue.put(case_id)
            span.set_attribute("faa.cases_opened", len(result.opened_cases))

        self._metrics.events_ingested.labels("ingested").inc(len(result.ingested))
        self._metrics.events_ingested.labels("duplicate").inc(len(result.duplicates))
        self._metrics.ingest_seconds.observe(time.perf_counter() - started)
        log.info(
            "batch ingested",
            ingested=len(result.ingested),
            duplicates=len(result.duplicates),
            rescored=len(result.rescored_accounts),
            cases_opened=len(result.opened_cases),
        )
        return result

    async def confirm_fraud(self, account_id: str, decided_at: datetime, source: str) -> list[uuid.UUID]:
        """Records a Confirmed Fraud in its own transaction and queues any Cases its Neighbours' re-scoring opened."""

        async def work(session: AsyncSession) -> list[uuid.UUID]:
            return await self.confirm_fraud_in(session, account_id, decided_at, source)

        opened = await self._db.run_transaction(work)
        for case_id in opened:
            await self._queue.put(case_id)
        return opened

    async def confirm_fraud_in(
        self, session: AsyncSession, account_id: str, decided_at: datetime, source: str
    ) -> list[uuid.UUID]:
        """Records a Confirmed Fraud inside the caller's transaction and re-scores the Accounts sharing an identifier
        with it, since each may now have a fraud-proximity hit. Returns Cases opened; the caller queues them after
        commit. Recording an Account twice keeps the first decision and re-scores nothing."""
        await self._db.lock_accounts(session, [account_id])
        if not await ConfirmedFraudRepository(session).record(account_id, decided_at, source):
            return []
        if not self._pack.rules.uses_graph:
            return []
        graph = LinkRepository(session)
        kinds = [i.kind for i in self._pack.pack.linking_identifiers]
        touched = await neighbours(await graph.links_of(account_id, kinds), [account_id], graph, kinds, 1)
        await self._db.lock_accounts(session, touched)
        opened: list[uuid.UUID] = []
        for other in touched:
            opened.extend(await self._rescore(session, other))
        log.info("confirmed fraud recorded", account_id=account_id, source=source, rescored=len(touched))
        return opened

    async def rebuild_links(self) -> int:
        """Rebuilds the Link Graph from the event store. Alerts are append-only, so nothing is re-scored."""
        count = 0
        async with self._db.transaction() as session:
            links = LinkRepository(session)
            await links.clear()
            async for batch in EventRepository(session).stream_in_order():
                await links.upsert(links_from(batch, self._pack.pack))
                count += len(batch)
        log.info("link graph rebuilt", events=count)
        return count

    def _validate(self, events: Sequence[Event]) -> None:
        if not events:
            raise InvalidInputError("a batch needs at least one event")
        if len(events) > MAX_BATCH:
            raise InvalidInputError(f"a batch holds at most {MAX_BATCH} events")
        unknown = sorted({e.type for e in events} - self._pack.pack.event_type_names)
        if unknown:
            raise InvalidInputError(f"unknown event types for pack {self._pack.pack.id}: {unknown}")

    async def _ingest_in(self, session: AsyncSession, events: Sequence[Event]) -> IngestResult:
        result = IngestResult()
        batch_accounts = sorted({e.account_id for e in events})
        await self._db.lock_accounts(session, batch_accounts)

        inserted = await EventRepository(session).append(events, self._clock())
        for e in events:
            (result.ingested if e.event_id in inserted else result.duplicates).append(e.event_id)

        # The whole batch is projected, duplicates included: a retry after a failed commit must still reach the graph.
        graph = LinkRepository(session)
        links = links_from(events, self._pack.pack)
        await graph.upsert(links)

        touched = list(batch_accounts)
        if self._pack.rules.uses_graph:
            kinds = [i.kind for i in self._pack.pack.linking_identifiers]
            extra = await neighbours(links, batch_accounts, graph, kinds, ring_hops(self._pack.rules))
            # Neighbours are only known after the links exist, so their locks come second, again in sorted order.
            await self._db.lock_accounts(session, extra)
            touched += extra

        for account_id in touched:
            result.opened_cases.extend(await self._rescore(session, account_id))
        result.rescored_accounts = touched
        return result

    async def _rescore(self, session: AsyncSession, account_id: str) -> list[uuid.UUID]:
        rules = self._pack.rules
        with tracer().start_as_current_span("detect") as span:
            span.set_attribute("faa.account_id", account_id)
            timeline = await EventRepository(session).timeline(account_id)
            alerts = AlertRepository(session)
            stored = await alerts.for_account(account_id)
            hits = (
                await graph_hits(account_id, rules, self._pack.pack, LinkRepository(session))
                if rules.uses_graph
                else []
            )
            detected = detect(timeline, rules, [s.alert for s in stored], hits)
            now = self._clock()
            changes = await alerts.save(account_id, stored, detected, rules.version, now)

        cases = CaseRepository(session)
        opened: list[uuid.UUID] = []
        for change in changes:
            for hit in change.new_hits:
                self._metrics.rule_hits.labels(hit.rule_id).inc()
            if change.previous_band != change.alert.band:
                self._metrics.alert_bands.labels(change.alert.band.value).inc()
            if change.alert.band < RiskBand.HIGH:
                continue
            existing = await cases.for_alert(change.alert_id)
            if existing is None:
                case = await cases.open(
                    change.alert_id, account_id, change.alert.band, change.alert.score, now
                )
                opened.append(case.case_id)
                self._metrics.cases_opened.inc()
                log.info(
                    "case opened",
                    case_id=str(case.case_id),
                    account_id=account_id,
                    band=change.alert.band.value,
                )
            else:
                # Reaching Critical later raises the Case's priority; it never opens a second Case or Investigation.
                await cases.raise_priority(existing, change.alert.band, change.alert.score, now)
        return opened
