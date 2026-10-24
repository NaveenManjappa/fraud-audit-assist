"""Offline replay: live ingestion's scoring loop over an in-memory Link Graph.

Events are applied one batch at a time in the order given, each Account and its Neighbours re-scored after every
batch with their previous Alerts as `stored`, exactly as ingestion does. Replaying in OccurredAt order therefore
never lets a graph rule see a link from the future, and dry runs score graph rules too.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from fraud_audit_assist.detection.detector import detect
from fraud_audit_assist.domain.alerts import Alert
from fraud_audit_assist.domain.events import Event
from fraud_audit_assist.domain.packs import DomainPack
from fraud_audit_assist.domain.rules import RuleSet
from fraud_audit_assist.graph.links import links_from
from fraud_audit_assist.graph.rules import graph_hits, neighbours
from fraud_audit_assist.graph.store import InMemoryLinkGraph


def ring_hops(rules: RuleSet) -> int:
    """How far a new link can change graph hits: the widest ring rule's reach, and at least one hop."""
    hops = [r.max_hops for r in rules.graph_rules if hasattr(r, "max_hops")]
    return max([1, *hops])


@dataclass
class OfflineReplay:
    pack: DomainPack
    rules: RuleSet
    graph: InMemoryLinkGraph = field(default_factory=InMemoryLinkGraph)
    timelines: dict[str, list[Event]] = field(default_factory=lambda: defaultdict(list))
    alerts: dict[str, list[Alert]] = field(default_factory=dict)
    _seen: set[UUID] = field(default_factory=set)

    async def ingest(self, batch: Sequence[Event]) -> list[str]:
        """Applies one batch and returns every Account re-scored, batch Accounts first."""
        for event in batch:
            if event.event_id not in self._seen:
                self.timelines[event.account_id].append(event)
                self._seen.add(event.event_id)
        links = links_from(batch, self.pack)
        self.graph.add(links)

        batch_accounts = sorted({e.account_id for e in batch})
        touched = batch_accounts
        if self.rules.uses_graph:
            kinds = [i.kind for i in self.pack.linking_identifiers]
            touched = batch_accounts + await neighbours(
                links, batch_accounts, self.graph, kinds, ring_hops(self.rules)
            )
        for account_id in touched:
            await self._rescore(account_id)
        return touched

    async def confirm_fraud(self, account_id: str) -> list[str]:
        """Records a Confirmed Fraud and re-scores the Accounts sharing an identifier with it, which may gain G2."""
        self.graph.mark_confirmed_fraud(account_id)
        if not self.rules.uses_graph:
            return []
        kinds = [i.kind for i in self.pack.linking_identifiers]
        own = await self.graph.links_of(account_id, kinds)
        touched = await neighbours(own, [account_id], self.graph, kinds, 1)
        for other in touched:
            await self._rescore(other)
        return touched

    async def _rescore(self, account_id: str) -> None:
        hits = (
            await graph_hits(account_id, self.rules, self.pack, self.graph) if self.rules.uses_graph else []
        )
        self.alerts[account_id] = detect(
            self.timelines[account_id], self.rules, stored=self.alerts.get(account_id, []), graph_hits=hits
        )


async def replay_in_time_order(
    replay: OfflineReplay, events: Iterable[Event], confirmed_frauds: Iterable[tuple[str, datetime]] = ()
) -> OfflineReplay:
    """Replays events one at a time in OccurredAt order, with each Confirmed Fraud applied at its decision time."""
    ordered = sorted(events, key=lambda e: (e.occurred_at, str(e.event_id)))
    frauds = sorted(confirmed_frauds, key=lambda f: f[1])
    for event in ordered:
        while frauds and frauds[0][1] <= event.occurred_at:
            await replay.confirm_fraud(frauds.pop(0)[0])
        await replay.ingest([event])
    for account_id, _ in frauds:
        await replay.confirm_fraud(account_id)
    return replay
