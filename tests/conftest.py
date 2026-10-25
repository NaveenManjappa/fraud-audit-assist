from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import pytest

from fraud_audit_assist.domain.events import Event
from fraud_audit_assist.domain.packs import LoadedPack, load_pack

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKS_DIR = REPO_ROOT / "packs"
T0 = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
_NS = UUID("6f1d3a52-6c43-4a8e-9a5e-0b1f7c3c2d10")


@pytest.fixture(scope="session")
def investor() -> LoadedPack:
    return load_pack(PACKS_DIR / "investor_portal")


@pytest.fixture(scope="session")
def banking() -> LoadedPack:
    return load_pack(PACKS_DIR / "digital_banking")


EventFactory = Callable[..., Event]


@pytest.fixture
def make_event() -> EventFactory:
    """Builds events with readable defaults; `minutes` is the offset from T0."""
    counter = {"n": 0}

    def make(
        type_: str,
        minutes: float = 0,
        *,
        account: str = "ACC-1",
        customer: str | None = None,
        device: str | None = "D-HOME",
        country: str | None = "GB",
        ip: str | None = "203.0.113.1",
        channel: str = "web",
        event_id: UUID | None = None,
        **attributes: Any,
    ) -> Event:
        counter["n"] += 1
        return Event(
            event_id=event_id or uuid5(_NS, f"{account}/{counter['n']}"),
            account_id=account,
            customer_id=customer or f"CUST-{account}",
            type=type_,
            occurred_at=T0 + timedelta(minutes=minutes),
            device_id=device,
            ip_address=ip,
            country=country,
            channel=channel,
            attributes=attributes,
        )

    return make
