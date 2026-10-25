"""The canonical event every source system is normalised into."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

AttributeValue = str | int | float | bool


def _require_aware(value: datetime) -> datetime:
    # Detection compares event times across sources; a naive time would silently shift by the server's zone.
    if value.tzinfo is None:
        raise ValueError("occurred_at must include a timezone offset")
    return value.astimezone(UTC)


AwareDatetime = Annotated[datetime, AfterValidator(_require_aware)]

# Core fields that rules and identifiers may name directly; anything else is read from `attributes`.
CORE_FIELDS = frozenset(
    {"type", "channel", "country", "device_id", "ip_address", "account_id", "customer_id"}
)


class Event(BaseModel):
    """One recorded action on an Account at a moment in time.

    `type` is validated against the active Domain Pack at ingestion, not here, so the same model serves every pack.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: UUID
    account_id: str = Field(min_length=1, max_length=128)
    customer_id: str = Field(min_length=1, max_length=128)
    type: str = Field(min_length=1, max_length=64)
    occurred_at: AwareDatetime
    device_id: str | None = Field(default=None, max_length=256)
    ip_address: str | None = Field(default=None, max_length=64)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    channel: str = Field(default="web", min_length=1, max_length=32)
    attributes: dict[str, AttributeValue] = Field(default_factory=dict)

    def field(self, name: str) -> AttributeValue | None:
        """Reads a core field (`country`) or an attribute (`attributes.amount`) by the name rules use."""
        if name.startswith("attributes."):
            return self.attributes.get(name.removeprefix("attributes."))
        if name in CORE_FIELDS:
            value: AttributeValue | None = getattr(self, name)
            return value
        raise KeyError(f"Unknown event field '{name}'")


def in_timeline_order(events: list[Event]) -> list[Event]:
    """Distinct events by `event_id`, ordered by when they happened at the source, never by arrival."""
    unique = {e.event_id: e for e in events}
    return sorted(unique.values(), key=lambda e: (e.occurred_at, str(e.event_id)))
