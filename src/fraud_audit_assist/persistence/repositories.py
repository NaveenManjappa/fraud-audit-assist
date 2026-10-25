"""Data access, one repository per aggregate. Each works inside a session the caller's transaction owns."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession

from fraud_audit_assist.domain.alerts import Alert, RuleHit
from fraud_audit_assist.domain.cases import CaseStatus
from fraud_audit_assist.domain.events import Event
from fraud_audit_assist.domain.rules import RiskBand
from fraud_audit_assist.graph.links import Link
from fraud_audit_assist.persistence.tables import (
    AlertRow,
    AnalystDecisionRow,
    AuditEntryRow,
    CaseRow,
    ConfirmedFraudRow,
    EventRow,
    IdentifierLinkRow,
    InterventionRow,
    RuleHitRow,
)


def _insert(session: AsyncSession, table: Any) -> Any:
    """The dialect's INSERT, which supports ON CONFLICT on both PostgreSQL and SQLite."""
    dialect = session.get_bind().dialect.name
    return postgresql.insert(table) if dialect == "postgresql" else sqlite.insert(table)


def to_event(row: EventRow) -> Event:
    return Event(
        event_id=row.event_id,
        account_id=row.account_id,
        customer_id=row.customer_id,
        type=row.type,
        occurred_at=row.occurred_at,
        device_id=row.device_id,
        ip_address=row.ip_address,
        country=row.country,
        channel=row.channel,
        attributes=row.attributes,
    )


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, events: Sequence[Event], received_at: datetime) -> set[uuid.UUID]:
        """Inserts events not already stored and returns the ids it inserted. A re-sent event is kept as first sent."""
        if not events:
            return set()
        rows = [
            {**e.model_dump(exclude={"attributes"}), "attributes": e.attributes, "received_at": received_at}
            for e in events
        ]
        statement = (
            _insert(self._session, EventRow)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["event_id"])
            .returning(EventRow.event_id)
        )
        result = await self._session.execute(statement)
        return set(result.scalars())

    async def timeline(
        self, account_id: str, since: datetime | None = None, until: datetime | None = None
    ) -> list[Event]:
        query = select(EventRow).where(EventRow.account_id == account_id)
        if since is not None:
            query = query.where(EventRow.occurred_at >= since)
        if until is not None:
            query = query.where(EventRow.occurred_at <= until)
        rows = await self._session.scalars(query.order_by(EventRow.occurred_at, EventRow.event_id))
        return [to_event(r) for r in rows]

    async def get_many(self, event_ids: Iterable[uuid.UUID]) -> list[Event]:
        ids = list(set(event_ids))
        if not ids:
            return []
        rows = await self._session.scalars(
            select(EventRow)
            .where(EventRow.event_id.in_(ids))
            .order_by(EventRow.occurred_at, EventRow.event_id)
        )
        return [to_event(r) for r in rows]

    async def latest_time(self, account_id: str) -> datetime | None:
        latest: datetime | None = await self._session.scalar(
            select(func.max(EventRow.occurred_at)).where(EventRow.account_id == account_id)
        )
        return latest

    async def stream_in_order(self, batch_size: int = 1000) -> AsyncIterator[list[Event]]:
        """Every event in OccurredAt order, streamed in batches so a rebuild never loads the store into memory."""
        query = (
            select(EventRow)
            .order_by(EventRow.occurred_at, EventRow.event_id)
            .execution_options(yield_per=batch_size)
        )
        result = await self._session.stream_scalars(query)
        async for partition in result.partitions(batch_size):
            yield [to_event(r) for r in partition]


class LinkRepository:
    """The Link Graph in the event store's own database, written in the ingest transaction (ADR-0015)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(self, links: Sequence[Link]) -> None:
        """Adds links, keeping the earliest use of each so the graph is the same whatever the replay order."""
        if not links:
            return
        insert = _insert(self._session, IdentifierLinkRow)
        statement = insert.values([link.model_dump() for link in links])
        excluded = statement.excluded
        statement = statement.on_conflict_do_update(
            index_elements=["account_id", "kind", "value"],
            set_={"first_seen": excluded.first_seen, "first_event_id": excluded.first_event_id},
            where=or_(
                excluded.first_seen < IdentifierLinkRow.first_seen,
                (excluded.first_seen == IdentifierLinkRow.first_seen)
                & (excluded.first_event_id < IdentifierLinkRow.first_event_id),
            ),
        )
        await self._session.execute(statement)

    async def links_of(self, account_id: str, kinds: Iterable[str]) -> list[Link]:
        rows = await self._session.scalars(
            select(IdentifierLinkRow)
            .where(IdentifierLinkRow.account_id == account_id, IdentifierLinkRow.kind.in_(list(kinds)))
            .order_by(IdentifierLinkRow.kind, IdentifierLinkRow.value)
        )
        return [self._link(r) for r in rows]

    async def links_on(self, kind: str, value: str) -> list[Link]:
        rows = await self._session.scalars(
            select(IdentifierLinkRow)
            .where(IdentifierLinkRow.kind == kind, IdentifierLinkRow.value == value)
            .order_by(
                IdentifierLinkRow.first_seen, IdentifierLinkRow.first_event_id, IdentifierLinkRow.account_id
            )
        )
        return [self._link(r) for r in rows]

    async def confirmed_fraud(self, account_ids: Iterable[str]) -> set[str]:
        ids = list(set(account_ids))
        if not ids:
            return set()
        rows = await self._session.scalars(
            select(ConfirmedFraudRow.account_id).where(ConfirmedFraudRow.account_id.in_(ids))
        )
        return set(rows)

    async def clear(self) -> None:
        await self._session.execute(delete(IdentifierLinkRow))

    @staticmethod
    def _link(row: IdentifierLinkRow) -> Link:
        return Link(
            account_id=row.account_id,
            customer_id=row.customer_id,
            kind=row.kind,
            value=row.value,
            first_seen=row.first_seen,
            first_event_id=row.first_event_id,
        )


class ConfirmedFraudRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, account_id: str, decided_at: datetime, source: str) -> bool:
        """Records a Confirmed Fraud once; recording it again keeps the first decision. True when newly recorded."""
        statement = (
            _insert(self._session, ConfirmedFraudRow)
            .values(account_id=account_id, decided_at=decided_at, source=source)
            .on_conflict_do_nothing(index_elements=["account_id"])
            .returning(ConfirmedFraudRow.account_id)
        )
        return (await self._session.execute(statement)).first() is not None


@dataclass(frozen=True)
class StoredAlert:
    alert_id: uuid.UUID
    alert: Alert


@dataclass(frozen=True)
class AlertChange:
    alert_id: uuid.UUID
    alert: Alert
    previous_band: RiskBand | None
    new_hits: list[RuleHit]


class AlertRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def for_account(self, account_id: str) -> list[StoredAlert]:
        alerts = (
            await self._session.scalars(
                select(AlertRow).where(AlertRow.account_id == account_id).order_by(AlertRow.sequence)
            )
        ).all()
        if not alerts:
            return []
        hits = (
            await self._session.scalars(
                select(RuleHitRow)
                .where(RuleHitRow.alert_id.in_([a.alert_id for a in alerts]))
                .order_by(RuleHitRow.alert_id, RuleHitRow.position)
            )
        ).all()
        by_alert: dict[uuid.UUID, list[RuleHit]] = {}
        for h in hits:
            by_alert.setdefault(h.alert_id, []).append(_to_hit(h))
        return [
            StoredAlert(
                a.alert_id,
                Alert(
                    account_id=a.account_id,
                    rules_version=a.rules_version,
                    hits=by_alert[a.alert_id],
                    score=a.score,
                    band=RiskBand(a.band),
                ),
            )
            for a in alerts
        ]

    async def save(
        self,
        account_id: str,
        stored: Sequence[StoredAlert],
        detected: Sequence[Alert],
        rules_version: str,
        now: datetime,
    ) -> list[AlertChange]:
        """Appends what detection added past the stored hits and inserts new Alerts. Stored hits are never touched."""
        changes: list[AlertChange] = []
        for sequence, alert in enumerate(detected):
            if sequence < len(stored):
                previous = stored[sequence]
                new_hits = alert.hits[len(previous.alert.hits) :]
                if not new_hits:
                    continue
                alert_id = previous.alert_id
                previous_band: RiskBand | None = previous.alert.band
                await self._session.execute(
                    update(AlertRow)
                    .where(AlertRow.alert_id == alert_id)
                    .values(score=alert.score, band=alert.band.value, updated_at=now)
                )
                start = len(previous.alert.hits)
            else:
                alert_id = uuid.uuid4()
                previous_band = None
                new_hits = list(alert.hits)
                self._session.add(
                    AlertRow(
                        alert_id=alert_id,
                        account_id=account_id,
                        sequence=sequence,
                        rules_version=alert.rules_version,
                        score=alert.score,
                        band=alert.band.value,
                        opened_at=now,
                        updated_at=now,
                    )
                )
                await self._session.flush()
                start = 0
            for offset, hit in enumerate(new_hits):
                self._session.add(
                    RuleHitRow(
                        alert_id=alert_id,
                        position=start + offset,
                        rule_id=hit.rule_id,
                        points=hit.points,
                        evidence_event_ids=[str(i) for i in hit.evidence_event_ids],
                        triggered_at=hit.triggered_at,
                        explanation=hit.explanation,
                        rules_version=rules_version,
                    )
                )
            changes.append(AlertChange(alert_id, alert, previous_band, new_hits))
        await self._session.flush()
        return changes

    async def get(self, alert_id: uuid.UUID) -> StoredAlert | None:
        row = await self._session.get(AlertRow, alert_id)
        if row is None:
            return None
        return next(s for s in await self.for_account(row.account_id) if s.alert_id == alert_id)

    async def page(
        self, band: RiskBand | None, account_id: str | None, limit: int, offset: int
    ) -> list[AlertRow]:
        query = select(AlertRow)
        if band is not None:
            query = query.where(AlertRow.band == band.value)
        if account_id is not None:
            query = query.where(AlertRow.account_id == account_id)
        query = query.order_by(_band_rank(AlertRow.band).desc(), AlertRow.score.desc(), AlertRow.opened_at)
        return list((await self._session.scalars(query.limit(limit).offset(offset))).all())


def _to_hit(row: RuleHitRow) -> RuleHit:
    return RuleHit(
        rule_id=row.rule_id,
        points=row.points,
        evidence_event_ids=[uuid.UUID(i) for i in row.evidence_event_ids],
        triggered_at=row.triggered_at,
        explanation=row.explanation,
    )


def _band_rank(column: Any) -> Any:
    return case({band.value: band.rank for band in RiskBand}, value=column, else_=-1)


class CaseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def for_alert(self, alert_id: uuid.UUID) -> CaseRow | None:
        return await self._session.scalar(select(CaseRow).where(CaseRow.alert_id == alert_id))

    async def open(
        self, alert_id: uuid.UUID, account_id: str, band: RiskBand, score: int, now: datetime
    ) -> CaseRow:
        row = CaseRow(
            case_id=uuid.uuid4(),
            alert_id=alert_id,
            account_id=account_id,
            status=CaseStatus.OPEN.value,
            band=band.value,
            score=score,
            opened_at=now,
            updated_at=now,
            rejection_reasons=[],
            pseudonym_map={},
            evidence_refs={},
            usage=[],
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def raise_priority(self, row: CaseRow, band: RiskBand, score: int, now: datetime) -> None:
        row.band, row.score, row.updated_at = band.value, score, now

    async def get(self, case_id: uuid.UUID, *, for_update: bool = False) -> CaseRow | None:
        query = select(CaseRow).where(CaseRow.case_id == case_id)
        if for_update and self._session.get_bind().dialect.name == "postgresql":
            query = query.with_for_update()
        return await self._session.scalar(query)

    async def page(self, status: CaseStatus | None, limit: int, offset: int) -> list[CaseRow]:
        query = select(CaseRow)
        if status is not None:
            query = query.where(CaseRow.status == status.value)
        query = query.order_by(_band_rank(CaseRow.band).desc(), CaseRow.score.desc(), CaseRow.opened_at)
        return list((await self._session.scalars(query.limit(limit).offset(offset))).all())

    async def claim(self, case_id: uuid.UUID, expected: CaseStatus, new: CaseStatus, now: datetime) -> bool:
        """Moves a Case between statuses only if it is still in `expected`: the loser of a race gets False."""
        result = await self._session.execute(
            update(CaseRow)
            .where(CaseRow.case_id == case_id, CaseRow.status == expected.value)
            .values(status=new.value, updated_at=now)
        )
        return bool(getattr(result, "rowcount", 0))

    async def to_resume(self) -> list[uuid.UUID]:
        """Cases a crash may have left behind: Open or Investigating, except those the budget guard skipped on purpose."""
        rows = await self._session.scalars(
            select(CaseRow.case_id)
            .where(
                CaseRow.status.in_([CaseStatus.OPEN.value, CaseStatus.INVESTIGATING.value]),
                CaseRow.not_investigated.is_(False),
            )
            .order_by(CaseRow.opened_at)
        )
        return list(rows)

    async def release_stale_decisions(self, now: datetime) -> int:
        """Puts Cases a crash left mid-decision back to AwaitingApproval. Act is one transaction, so a Case still
        Deciding at startup has no decision recorded and can safely be decided again."""
        result = await self._session.execute(
            update(CaseRow)
            .where(CaseRow.status == CaseStatus.DECIDING.value)
            .values(status=CaseStatus.AWAITING_APPROVAL.value, updated_at=now)
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def started_since(self, since: datetime) -> int:
        count = await self._session.scalar(
            select(func.count()).select_from(CaseRow).where(CaseRow.investigation_started_at >= since)
        )
        return int(count or 0)

    async def decision(self, case_id: uuid.UUID) -> AnalystDecisionRow | None:
        return await self._session.get(AnalystDecisionRow, case_id)

    async def interventions(self, case_id: uuid.UUID) -> list[InterventionRow]:
        rows = await self._session.scalars(select(InterventionRow).where(InterventionRow.case_id == case_id))
        return list(rows)


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, entry: AuditEntryRow) -> None:
        self._session.add(entry)

    async def for_case(self, case_id: uuid.UUID) -> list[AuditEntryRow]:
        rows = await self._session.scalars(
            select(AuditEntryRow).where(AuditEntryRow.case_id == case_id).order_by(AuditEntryRow.id)
        )
        return list(rows)
