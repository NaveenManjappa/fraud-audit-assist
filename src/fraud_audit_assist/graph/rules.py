"""Graph Rules: candidate Rule Hits from links between Accounts (ADR-0014).

Each rule reads the Link Graph as it is now and returns candidates; the Detector decides which count. A graph hit's
trigger time is when the pattern completed, which is when the latest link it needs first appeared, and its evidence
is the events behind those links, with the completing one last.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Sequence

from fraud_audit_assist.domain.alerts import RuleHit
from fraud_audit_assist.domain.packs import DomainPack
from fraud_audit_assist.domain.rules import (
    FraudProximityRule,
    GraphRule,
    RingRule,
    RuleSet,
    SharedIdentifierRule,
)
from fraud_audit_assist.graph.links import Link
from fraud_audit_assist.graph.store import LinkGraphReader

# A busy shared identifier (a kiosk device, a popular payee) could otherwise pull thousands of Accounts into one
# multi-hop exploration. Past this, the ring is already far over any sensible threshold.
MAX_EXPLORED_ACCOUNTS = 200
MAX_EVIDENCE_LINKS = 50


async def graph_hits(
    account_id: str, rules: RuleSet, pack: DomainPack, graph: LinkGraphReader
) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for rule in rules.graph_rules:
        hits.extend(await _hits_for_rule(rule, account_id, pack, graph))
    return hits


async def _hits_for_rule(
    rule: GraphRule, account_id: str, pack: DomainPack, graph: LinkGraphReader
) -> list[RuleHit]:
    if isinstance(rule, SharedIdentifierRule):
        return await _shared_identifier(rule, account_id, pack, graph)
    if isinstance(rule, FraudProximityRule):
        return await _fraud_proximity(rule, account_id, pack, graph)
    if rule.max_hops == 1:
        return await _ring_one_hop(rule, account_id, pack, graph)
    return await _ring_multi_hop(rule, account_id, pack, graph)


async def _shared_identifier(
    rule: SharedIdentifierRule, account_id: str, pack: DomainPack, graph: LinkGraphReader
) -> list[RuleHit]:
    hits = []
    for mine in await graph.links_of(account_id, rule.identifier_kinds):
        for theirs in await graph.links_on(mine.kind, mine.value):
            if theirs.account_id == account_id:
                continue
            if rule.distinct_customer and theirs.customer_id == mine.customer_id:
                continue
            explanation = rule.explain(
                identifier=pack.identifier(mine.kind).label.lower(),
                value=mine.value,
                account=theirs.account_id,
            )
            hits.append(_pair_hit(rule, mine, theirs, explanation))
    return hits


async def _fraud_proximity(
    rule: FraudProximityRule, account_id: str, pack: DomainPack, graph: LinkGraphReader
) -> list[RuleHit]:
    hits = []
    for mine in await graph.links_of(account_id, rule.identifier_kinds):
        others = [
            link for link in await graph.links_on(mine.kind, mine.value) if link.account_id != account_id
        ]
        fraud = await graph.confirmed_fraud(link.account_id for link in others)
        # The Confirmed Fraud decision is not an event, so the links alone set the trigger time: a decision made
        # after the link lifts the Account at the link's time, a late hit like any other (ADR-0013).
        for theirs in (link for link in others if link.account_id in fraud):
            explanation = rule.explain(
                identifier=pack.identifier(mine.kind).label.lower(),
                value=mine.value,
                account=theirs.account_id,
            )
            hits.append(_pair_hit(rule, mine, theirs, explanation))
    return hits


def _pair_hit(rule: GraphRule, mine: Link, theirs: Link, explanation: str) -> RuleHit:
    # Whichever link formed second completed the pair. On a tie this Account's own link goes last, since it is
    # the one being scored.
    first, completing = (mine, theirs) if theirs.first_seen > mine.first_seen else (theirs, mine)
    return RuleHit(
        rule_id=rule.id,
        points=rule.points,
        evidence_event_ids=[first.first_event_id, completing.first_event_id],
        triggered_at=completing.first_seen,
        explanation=explanation,
    )


async def _ring_one_hop(
    rule: RingRule, account_id: str, pack: DomainPack, graph: LinkGraphReader
) -> list[RuleHit]:
    """One identifier shared by at least the threshold number of Accounts, this one included.

    The group reached the threshold when the latest of this Account's link and the earliest links of
    `threshold - 1` others appeared, so the third Account's own link triggers it and lifts the first two as well.
    """
    hits = []
    for mine in await graph.links_of(account_id, rule.identifier_kinds):
        on_identifier = await graph.links_on(mine.kind, mine.value)
        others = sorted(
            (link for link in on_identifier if link.account_id != account_id),
            key=lambda link: (link.first_seen, link.account_id),
        )
        if len(others) + 1 < rule.account_threshold:
            continue
        # sorted() is stable, so on a tie this Account's own link, appended last, stays last.
        group = sorted([*others[: rule.account_threshold - 1], mine], key=lambda link: link.first_seen)
        hits.append(
            RuleHit(
                rule_id=rule.id,
                points=rule.points,
                evidence_event_ids=[link.first_event_id for link in group],
                triggered_at=group[-1].first_seen,
                explanation=rule.explain(
                    identifier=pack.identifier(mine.kind).label.lower(),
                    value=mine.value,
                    accounts=len(others) + 1,
                    threshold=rule.account_threshold,
                    hops=1,
                ),
            )
        )
    return hits


async def _ring_multi_hop(
    rule: RingRule, account_id: str, pack: DomainPack, graph: LinkGraphReader
) -> list[RuleHit]:
    """A group of at least the threshold number of Accounts reachable within `max_hops` shared identifiers.

    This catches chains (A and B share a device, B and C share a phone) that one-hop rings miss. Links are replayed
    in the order they appeared, and the hit triggers on the first link after which the group within reach of this
    Account reached the threshold.
    """
    edges = await _explore(account_id, rule.identifier_kinds, rule.max_hops, graph)
    if not any(link.account_id == account_id for link in edges):
        return []
    ordered = sorted(edges, key=Link.sort_key)
    total = len(_reach(account_id, ordered, rule.max_hops))
    if total < rule.account_threshold:
        return []
    for k in range(len(ordered)):
        paths = _reach(account_id, ordered[: k + 1], rule.max_hops)
        if len(paths) < rule.account_threshold:
            continue
        completing = ordered[k]
        on_paths = {link for path in paths.values() for link in path} - {completing}
        evidence = [*sorted(on_paths, key=Link.sort_key)[-(MAX_EVIDENCE_LINKS - 1) :], completing]
        return [
            RuleHit(
                rule_id=rule.id,
                points=rule.points,
                evidence_event_ids=list(dict.fromkeys(link.first_event_id for link in evidence)),
                triggered_at=completing.first_seen,
                explanation=rule.explain(
                    identifier=pack.identifier(completing.kind).label.lower(),
                    value=completing.value,
                    accounts=total,
                    threshold=rule.account_threshold,
                    hops=rule.max_hops,
                ),
            )
        ]
    return []  # pragma: no cover - the full edge list reaches `total`, so the loop always returns


async def _explore(account_id: str, kinds: Sequence[str], max_hops: int, graph: LinkGraphReader) -> set[Link]:
    edges: set[Link] = set()
    seen = {account_id}
    frontier = [account_id]
    for _ in range(max_hops):
        next_frontier: list[str] = []
        for current in frontier:
            for mine in await graph.links_of(current, kinds):
                for link in await graph.links_on(mine.kind, mine.value):
                    edges.add(link)
                    if link.account_id not in seen and len(seen) < MAX_EXPLORED_ACCOUNTS:
                        seen.add(link.account_id)
                        next_frontier.append(link.account_id)
        frontier = next_frontier
    return edges


def _reach(account_id: str, links: Iterable[Link], max_hops: int) -> dict[str, list[Link]]:
    """Accounts within `max_hops` of this one over the given links, each with the links on one shortest path."""
    by_account: dict[str, list[Link]] = {}
    by_identifier: dict[tuple[str, str], list[Link]] = {}
    for link in links:
        by_account.setdefault(link.account_id, []).append(link)
        by_identifier.setdefault(link.identifier, []).append(link)

    paths: dict[str, list[Link]] = {account_id: []}
    queue = deque([(account_id, 0)])
    while queue:
        current, hops = queue.popleft()
        if hops == max_hops:
            continue
        for mine in by_account.get(current, []):
            for theirs in by_identifier[mine.identifier]:
                if theirs.account_id not in paths:
                    paths[theirs.account_id] = [*paths[current], mine, theirs]
                    queue.append((theirs.account_id, hops + 1))
    return paths


async def neighbours(
    links: Iterable[Link], exclude: Iterable[str], graph: LinkGraphReader, kinds: Sequence[str], hops: int = 1
) -> list[str]:
    """Accounts outside `exclude` within `hops` shared identifiers of the given links (ADR-0015).

    A new link can change their graph hits, so ingestion re-checks them in the same transaction. Sorted, so locks
    on them are always taken in the same order.
    """
    excluded = set(exclude)
    found: set[str] = set()
    identifiers = {link.identifier for link in links}
    for _ in range(hops):
        reached: set[str] = set()
        for kind, value in identifiers:
            reached.update(link.account_id for link in await graph.links_on(kind, value))
        new_accounts = reached - found - excluded
        found |= new_accounts
        if len(found) >= MAX_EXPLORED_ACCOUNTS or not new_accounts:
            break
        identifiers = {
            link.identifier for account in new_accounts for link in await graph.links_of(account, kinds)
        }
    return sorted(found - excluded)
