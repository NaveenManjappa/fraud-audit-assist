"""Groups Rule Hits into Risk Episodes, one Alert each (ADR-0013).

A hit joins the episode of the previous hit if it triggered at most the risk period later; otherwise it opens a
new episode. A Rule counts once per episode, so the Score can only grow and a band can only rise.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import timedelta

from fraud_audit_assist.domain.alerts import RuleHit


def assemble(
    stored: Sequence[Sequence[RuleHit]], candidates: Iterable[RuleHit], risk_period: timedelta
) -> list[list[RuleHit]]:
    """Returns the stored episodes, extended, followed by any new ones.

    `candidates` must be in trigger-time order. Stored hits are never removed or reordered, so the evidence behind
    an Alert an analyst has seen never changes.
    """
    episodes = [list(episode) for episode in stored]
    for hit in candidates:
        home = _episode_for(hit, episodes, risk_period)
        if home is None:
            episodes.append([hit])
        elif all(existing.rule_id != hit.rule_id for existing in home):
            home.append(hit)
        # A repeat of a Rule already in the episode is dropped, not just left unscored, so a Rule that keeps
        # firing cannot hold an episode open on its own.
    return episodes


def _episode_for(hit: RuleHit, episodes: list[list[RuleHit]], risk_period: timedelta) -> list[RuleHit] | None:
    placed = [(episode, h.triggered_at) for episode in episodes for h in episode]

    previous = [
        (e, at) for e, at in placed if at <= hit.triggered_at and hit.triggered_at - at <= risk_period
    ]
    if previous:
        return max(previous, key=lambda p: p[1])[0]

    # Candidates arrive in trigger order, so a placed hit can only lie ahead of this one when a late event completed
    # a pattern after its episode was stored. It then joins the episode it would have started.
    ahead = [(e, at) for e, at in placed if at > hit.triggered_at and at - hit.triggered_at <= risk_period]
    if ahead:
        return min(ahead, key=lambda p: p[1])[0]
    return None
