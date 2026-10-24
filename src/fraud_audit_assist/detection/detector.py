"""The rules engine's single entry point.

`detect` is a pure function: the same timeline, Rule Set, stored Alerts and graph hits always give the same Alerts,
with no database and no clock, so live ingestion, dry runs and offline replay share it unchanged (ADR-0001).
"""

from __future__ import annotations

from collections.abc import Sequence

from fraud_audit_assist.detection.episodes import assemble
from fraud_audit_assist.detection.matchers import match_first_sighting, match_sequence, match_velocity
from fraud_audit_assist.domain.alerts import Alert, RuleHit
from fraud_audit_assist.domain.events import Event, in_timeline_order
from fraud_audit_assist.domain.rules import FirstSightingRule, RuleSet, SequenceRule, VelocityRule


def detect(
    timeline: Sequence[Event],
    rules: RuleSet,
    stored: Sequence[Alert] = (),
    graph_hits: Sequence[RuleHit] = (),
) -> list[Alert]:
    """Scores one Account.

    `stored` are the Account's persisted Alerts: the result starts with them, in order and with their hits
    unchanged, followed by any new Alerts. `graph_hits` are candidate hits found over the Link Graph; they are
    placed into Risk Episodes like any other (ADR-0014).
    """
    ordered = in_timeline_order(list(timeline))
    if not ordered:
        return list(stored)

    own = sorted(_candidates(ordered, rules), key=lambda h: h.triggered_at)
    # Python's sort is stable, so on a tie the Account's own hits stay ahead of graph hits.
    merged = sorted([*own, *graph_hits], key=lambda h: h.triggered_at)
    episodes = assemble([a.hits for a in stored], merged, rules.risk_period)

    account_id = ordered[0].account_id
    alerts: list[Alert] = []
    for i, hits in enumerate(episodes):
        # A stored Alert keeps the Rules Version that opened it; only new Alerts take the current one.
        version = stored[i].rules_version if i < len(stored) else rules.version
        # Recomputed from the stored points, never from the current rules, so a past Score is reproducible.
        score = sum(h.points for h in hits)
        alerts.append(
            Alert(
                account_id=account_id,
                rules_version=version,
                hits=hits,
                score=score,
                band=rules.bands.band_for(score),
            )
        )
    return alerts


def _candidates(timeline: list[Event], rules: RuleSet) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for rule in rules.active:
        if isinstance(rule, SequenceRule):
            hits.extend(match_sequence(rule, timeline))
        elif isinstance(rule, FirstSightingRule):
            hits.extend(match_first_sighting(rule, timeline))
        elif isinstance(rule, VelocityRule):
            hits.extend(match_velocity(rule, timeline))
        # Graph rules are matched across Accounts by the graph module and passed in as graph_hits.
    return hits
