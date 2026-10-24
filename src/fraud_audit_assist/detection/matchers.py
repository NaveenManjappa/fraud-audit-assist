"""Per-Account matchers: each turns one Rule and one timeline into candidate Rule Hits.

Every matcher takes the timeline already in OccurredAt order and returns hits in that order. Which candidates
count is decided later by Risk Episode assembly, so a matcher reports every match it sees.
"""

from __future__ import annotations

from collections.abc import Sequence

from fraud_audit_assist.domain.alerts import RuleHit
from fraud_audit_assist.domain.durations import format_duration
from fraud_audit_assist.domain.events import Event
from fraud_audit_assist.domain.rules import FirstSightingRule, SequenceRule, VelocityRule

_FIELD_LABELS = {"device_id": "device", "ip_address": "IP", "country": "country", "channel": "channel"}


def match_sequence(rule: SequenceRule, timeline: Sequence[Event]) -> list[RuleHit]:
    """Each event that completes the rule's last step, with the latest earlier event for each prior step.

    Choosing the latest match for each earlier step, walking backwards, gives the shortest span the events can
    have, so if any combination fits the Window this one does. The Window is inclusive.
    """
    hits: list[RuleHit] = []
    for index, last in enumerate(timeline):
        if not rule.steps[-1].matches(last):
            continue
        chosen = _earlier_steps(rule, timeline, index)
        if chosen is None:
            continue
        elapsed = last.occurred_at - chosen[0].occurred_at
        hits.append(
            RuleHit(
                rule_id=rule.id,
                points=rule.points,
                evidence_event_ids=[e.event_id for e in [*chosen, last]],
                triggered_at=last.occurred_at,
                explanation=rule.explain(
                    elapsed=format_duration(elapsed), window=format_duration(rule.window)
                ),
            )
        )
    return hits


def _earlier_steps(rule: SequenceRule, timeline: Sequence[Event], index: int) -> list[Event] | None:
    last = timeline[index]
    chosen: list[Event] = []
    cursor = index
    for step in reversed(rule.steps[:-1]):
        found = None
        for j in range(cursor - 1, -1, -1):
            candidate = timeline[j]
            if last.occurred_at - candidate.occurred_at > rule.window:
                break
            if step.matches(candidate) and _joined(rule, candidate, last):
                found = j
                break
        if found is None:
            return None
        chosen.insert(0, timeline[found])
        cursor = found
    return chosen


def _joined(rule: SequenceRule, candidate: Event, last: Event) -> bool:
    # A missing join value never joins: "same payee" cannot be shown when one event names no payee.
    for name in rule.join_on:
        value = last.field(name)
        if value is None or candidate.field(name) != value:
            return False
    return True


def match_first_sighting(rule: FirstSightingRule, timeline: Sequence[Event]) -> list[RuleHit]:
    """Events carrying a value, in any of the rule's fields, that no earlier event of the Account carried.

    The first event is never a sighting, because there is nothing to compare it against. Several new values on
    one event are one hit: they are one sighting of one stranger, not separate signals.
    """
    seen: dict[str, set[str]] = {name: set() for name in rule.fields}
    hits: list[RuleHit] = []
    for index, event in enumerate(timeline):
        sightings: list[str] = []
        for name in rule.fields:
            raw = event.field(name)
            if raw is None or raw == "":
                continue
            value = str(raw)
            if index > 0 and value not in seen[name]:
                sightings.append(f"new {_label(name)} {value}")
            seen[name].add(value)
        if sightings:
            hits.append(
                RuleHit(
                    rule_id=rule.id,
                    points=rule.points,
                    evidence_event_ids=[event.event_id],
                    triggered_at=event.occurred_at,
                    explanation=rule.explain(sighting=", ".join(sightings)),
                )
            )
    return hits


def match_velocity(rule: VelocityRule, timeline: Sequence[Event]) -> list[RuleHit]:
    """Each matching event that brings the count of matching events inside the Window up to `count` or more."""
    matching = [e for e in timeline if rule.step.matches(e)]
    hits: list[RuleHit] = []
    start = 0
    for end, event in enumerate(matching):
        while event.occurred_at - matching[start].occurred_at > rule.window:
            start += 1
        in_window = matching[start : end + 1]
        if len(in_window) < rule.count:
            continue
        evidence = in_window[-rule.count :]
        hits.append(
            RuleHit(
                rule_id=rule.id,
                points=rule.points,
                evidence_event_ids=[e.event_id for e in evidence],
                triggered_at=event.occurred_at,
                explanation=rule.explain(
                    count=len(in_window),
                    window=format_duration(rule.window),
                    types=" or ".join(rule.step.types),
                ),
            )
        )
    return hits


def _label(field_name: str) -> str:
    if field_name.startswith("attributes."):
        return field_name.removeprefix("attributes.").replace("_", " ")
    return _FIELD_LABELS.get(field_name, field_name)
