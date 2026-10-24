"""Identifier Links: which Accounts used which Linking Identifier, and since when (ADR-0015)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from fraud_audit_assist.domain.events import AwareDatetime, Event
from fraud_audit_assist.domain.packs import DomainPack


class Link(BaseModel):
    """One Account's use of one Linking Identifier.

    Only the earliest use is kept (`first_seen`, with the lower event id breaking an exact tie), so links are
    order-independent: replaying events in any order, or rebuilding from the event store, gives the same graph.
    """

    model_config = ConfigDict(frozen=True)

    account_id: str
    customer_id: str
    kind: str
    value: str
    first_seen: AwareDatetime
    first_event_id: UUID

    @property
    def identifier(self) -> tuple[str, str]:
        return (self.kind, self.value)

    def sort_key(self) -> tuple[datetime, str]:
        return (self.first_seen, str(self.first_event_id))

    def earlier_of(self, other: Link) -> Link:
        return self if self.sort_key() <= other.sort_key() else other


def links_from(events: Iterable[Event], pack: DomainPack) -> list[Link]:
    """The Linking Identifier links the events carry, earliest per (account, kind, value)."""
    best: dict[tuple[str, str, str], Link] = {}
    for event in events:
        for spec in pack.linking_identifiers:
            value = spec.value_in(event)
            if value is None:
                continue
            link = Link(
                account_id=event.account_id,
                customer_id=event.customer_id,
                kind=spec.kind,
                value=value,
                first_seen=event.occurred_at,
                first_event_id=event.event_id,
            )
            key = (event.account_id, spec.kind, value)
            best[key] = link.earlier_of(best[key]) if key in best else link
    return list(best.values())
