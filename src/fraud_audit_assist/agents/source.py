"""What an Investigation reads, raw. Masking lives on the agents' side of this seam, so no source can forget it."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from fraud_audit_assist.detection.replay import OfflineReplay
from fraud_audit_assist.domain.alerts import Alert
from fraud_audit_assist.domain.events import Event
from fraud_audit_assist.domain.packs import DomainPack
from fraud_audit_assist.errors import NotFoundError
from fraud_audit_assist.graph.neighbourhood import LinkedIdentifier, neighbourhood
from fraud_audit_assist.persistence.db import Database
from fraud_audit_assist.persistence.repositories import (
    AlertRepository,
    CaseRepository,
    EventRepository,
    LinkRepository,
)


class CaseContext(BaseModel):
    """The Case being investigated. `as_of` bounds every read, so the agents never see an event from the future."""

    model_config = ConfigDict(frozen=True)

    case_id: uuid.UUID
    account_id: str
    alert: Alert
    as_of: datetime


class InvestigationSource(Protocol):
    async def case_context(self, case_id: uuid.UUID) -> CaseContext: ...

    async def events(self, event_ids: Iterable[uuid.UUID]) -> list[Event]: ...

    async def timeline(self, account_id: str, since: datetime | None, until: datetime) -> list[Event]: ...

    async def neighbourhood(self, account_id: str) -> list[LinkedIdentifier]: ...


class SqlInvestigationSource:
    """Reads the live event store and Link Graph, each call in its own short read session."""

    def __init__(self, db: Database, pack: DomainPack) -> None:
        self._db = db
        self._pack = pack

    async def case_context(self, case_id: uuid.UUID) -> CaseContext:
        async with self._db.read() as session:
            case = await CaseRepository(session).get(case_id)
            if case is None:
                raise NotFoundError(f"case {case_id} not found")
            stored = await AlertRepository(session).get(case.alert_id)
            latest = await EventRepository(session).latest_time(case.account_id)
        if stored is None or latest is None:
            raise NotFoundError(f"case {case_id} has no alert or events")
        return CaseContext(case_id=case_id, account_id=case.account_id, alert=stored.alert, as_of=latest)

    async def events(self, event_ids: Iterable[uuid.UUID]) -> list[Event]:
        async with self._db.read() as session:
            return await EventRepository(session).get_many(event_ids)

    async def timeline(self, account_id: str, since: datetime | None, until: datetime) -> list[Event]:
        async with self._db.read() as session:
            return await EventRepository(session).timeline(account_id, since, until)

    async def neighbourhood(self, account_id: str) -> list[LinkedIdentifier]:
        async with self._db.read() as session:
            return await neighbourhood(account_id, self._pack, LinkRepository(session))


class ReplayInvestigationSource:
    """Reads an offline replay, for tests and offline evaluation. Cases are registered with the moment they opened."""

    def __init__(self, replay: OfflineReplay) -> None:
        self._replay = replay
        self._cases: dict[uuid.UUID, CaseContext] = {}

    def add_case(self, account_id: str, alert: Alert, as_of: datetime) -> uuid.UUID:
        case_id = uuid.uuid4()
        self._cases[case_id] = CaseContext(case_id=case_id, account_id=account_id, alert=alert, as_of=as_of)
        return case_id

    async def case_context(self, case_id: uuid.UUID) -> CaseContext:
        if case_id not in self._cases:
            raise NotFoundError(f"case {case_id} not found")
        return self._cases[case_id]

    async def events(self, event_ids: Iterable[uuid.UUID]) -> list[Event]:
        wanted = set(event_ids)
        found = [e for timeline in self._replay.timelines.values() for e in timeline if e.event_id in wanted]
        return sorted(found, key=lambda e: (e.occurred_at, str(e.event_id)))

    async def timeline(self, account_id: str, since: datetime | None, until: datetime) -> list[Event]:
        events = self._replay.timelines.get(account_id, [])
        return sorted(
            (e for e in events if (since is None or e.occurred_at >= since) and e.occurred_at <= until),
            key=lambda e: (e.occurred_at, str(e.event_id)),
        )

    async def neighbourhood(self, account_id: str) -> list[LinkedIdentifier]:
        return await neighbourhood(account_id, self._replay.pack, self._replay.graph)
