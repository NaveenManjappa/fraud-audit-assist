from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from fraud_audit_assist.detection.replay import OfflineReplay
from fraud_audit_assist.domain.cases import CaseStatus
from fraud_audit_assist.domain.packs import LoadedPack
from fraud_audit_assist.domain.rules import RiskBand
from fraud_audit_assist.domain.scenarios import load_scenarios
from fraud_audit_assist.errors import InvalidInputError
from fraud_audit_assist.observability.metrics import Metrics
from fraud_audit_assist.persistence.db import Database
from fraud_audit_assist.persistence.repositories import AlertRepository, CaseRepository
from fraud_audit_assist.services.case_queue import InProcessCaseQueue
from fraud_audit_assist.services.ingestion import IngestionService
from tests.conftest import PACKS_DIR, EventFactory


def service(db: Database, pack: LoadedPack, queue: InProcessCaseQueue | None = None) -> IngestionService:
    return IngestionService(db, pack, queue or InProcessCaseQueue(), Metrics())


async def post_investor_scenarios(ingestion: IngestionService, pack: LoadedPack) -> None:
    for scenario in load_scenarios(PACKS_DIR / "investor_portal" / "scenarios", pack.pack):
        await ingestion.ingest(scenario.events())
        if scenario.confirmed_fraud:
            await ingestion.confirm_fraud(scenario.account_id, scenario.confirmed_fraud, "Seed")


async def test_live_ingestion_matches_offline_replay(database: Database, investor: LoadedPack) -> None:
    ingestion = service(database, investor)
    await post_investor_scenarios(ingestion, investor)

    scenarios = load_scenarios(PACKS_DIR / "investor_portal" / "scenarios", investor.pack)
    replay = OfflineReplay(investor.pack, investor.rules)
    for scenario in scenarios:
        await replay.ingest(scenario.events())
        if scenario.confirmed_fraud:
            await replay.confirm_fraud(scenario.account_id)

    async with database.read() as session:
        for scenario in scenarios:
            stored = [s.alert for s in await AlertRepository(session).for_account(scenario.account_id)]
            assert stored == replay.alerts.get(scenario.account_id, []), scenario.name


async def test_margaret_opens_one_case_and_it_is_queued(database: Database, investor: LoadedPack) -> None:
    queue = InProcessCaseQueue()
    await post_investor_scenarios(service(database, investor, queue), investor)
    async with database.read() as session:
        cases = await CaseRepository(session).page(CaseStatus.OPEN, 50, 0)
    margaret = [c for c in cases if c.account_id == "ACC-MARGARET"]
    assert len(margaret) == 1
    assert margaret[0].band == RiskBand.CRITICAL.value
    queued = {queue._queue.get_nowait() for _ in range(queue.qsize())}
    assert margaret[0].case_id in queued


async def test_reposting_is_idempotent(
    database: Database, investor: LoadedPack, make_event: EventFactory
) -> None:
    ingestion = service(database, investor)
    events = [
        make_event("LoginSucceeded", 0),
        make_event("PasswordReset", 10, device="D-X"),
        make_event("MfaEnrolled", 12, device="D-X"),
    ]
    first = await ingestion.ingest(events)
    second = await ingestion.ingest(events)
    assert len(first.ingested) == 3
    assert second.duplicates == [e.event_id for e in events]
    assert second.opened_cases == []
    async with database.read() as session:
        [alert] = await AlertRepository(session).for_account("ACC-1")
    assert [h.rule_id for h in alert.alert.hits] == ["R0", "R1"]


async def test_unknown_event_type_is_rejected(
    database: Database, investor: LoadedPack, make_event: EventFactory
) -> None:
    with pytest.raises(InvalidInputError, match="Teleported"):
        await service(database, investor).ingest([make_event("Teleported", 0)])


async def test_concurrent_batches_on_one_account_lose_nothing(
    database: Database, investor: LoadedPack, make_event: EventFactory
) -> None:
    ingestion = service(database, investor)
    events = [make_event("LoginSucceeded", m) for m in range(20)]
    await asyncio.gather(*(ingestion.ingest([e]) for e in events))
    async with database.read() as session:
        from fraud_audit_assist.persistence.repositories import EventRepository

        assert len(await EventRepository(session).timeline("ACC-1")) == 20


async def test_rebuild_links_restores_the_graph(database: Database, investor: LoadedPack) -> None:
    ingestion = service(database, investor)
    await post_investor_scenarios(ingestion, investor)
    from fraud_audit_assist.persistence.repositories import LinkRepository

    async with database.read() as session:
        before = await LinkRepository(session).links_on("device", "D-77")
    await ingestion.rebuild_links()
    async with database.read() as session:
        after = await LinkRepository(session).links_on("device", "D-77")
    assert before == after
    assert len(after) == 4


async def test_confirming_fraud_twice_keeps_the_first(database: Database, investor: LoadedPack) -> None:
    ingestion = service(database, investor)
    await ingestion.confirm_fraud("ACC-X", datetime(2026, 1, 1, tzinfo=UTC), "Seed")
    assert await ingestion.confirm_fraud("ACC-X", datetime(2026, 2, 1, tzinfo=UTC), "Analyst") == []
