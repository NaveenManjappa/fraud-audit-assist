"""Dry runs: replay Scenarios in memory and show the Score and Risk Band after every step, graph rules included."""

from __future__ import annotations

from dataclasses import dataclass

from fraud_audit_assist.detection.replay import OfflineReplay
from fraud_audit_assist.domain.alerts import Alert
from fraud_audit_assist.domain.packs import LoadedPack
from fraud_audit_assist.domain.rules import RiskBand
from fraud_audit_assist.domain.scenarios import Scenario


@dataclass(frozen=True)
class StepResult:
    number: int
    offset: str
    event_type: str
    actor: str
    new_hits: list[str]
    score: int
    band: RiskBand


@dataclass(frozen=True)
class ScenarioResult:
    scenario: Scenario
    steps: list[StepResult]

    @property
    def peak(self) -> RiskBand:
        return max((s.band for s in self.steps), default=RiskBand.LOW)

    @property
    def first_high(self) -> StepResult | None:
        return next((s for s in self.steps if s.band >= RiskBand.HIGH), None)


async def dry_run(pack: LoadedPack, scenarios: list[Scenario]) -> list[ScenarioResult]:
    """Replays every Scenario in first-event order, as live ingestion would see them, one batch per step."""
    replay = OfflineReplay(pack.pack, pack.rules)
    results: list[ScenarioResult] = []
    for scenario in sorted(scenarios, key=lambda s: s.first_event_at()):
        if scenario.baseline is not None:
            await replay.ingest(scenario.events()[: scenario.baseline.weeks])
        steps: list[StepResult] = []
        for number, (step, event) in enumerate(
            zip(scenario.steps, scenario.step_events(), strict=True), start=1
        ):
            before = _hits(replay.alerts.get(scenario.account_id, []))
            await replay.ingest([event])
            alerts = replay.alerts.get(scenario.account_id, [])
            peak = max(alerts, key=lambda a: a.score, default=None)
            steps.append(
                StepResult(
                    number=number,
                    offset=_offset(step.after.total_seconds()),
                    event_type=event.type,
                    actor=step.actor,
                    new_hits=[h for h in _hits(alerts) if h not in before],
                    score=peak.score if peak else 0,
                    band=peak.band if peak else RiskBand.LOW,
                )
            )
        if scenario.confirmed_fraud:
            await replay.confirm_fraud(scenario.account_id)
        results.append(ScenarioResult(scenario, steps))
    return results


def _hits(alerts: list[Alert]) -> list[str]:
    return [f"{h.rule_id} +{h.points}: {h.explanation}" for a in alerts for h in a.hits]


def _offset(seconds: float) -> str:
    minutes = int(seconds // 60)
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    return "".join(f"{v}{u}" for v, u in ((days, "d"), (hours, "h"), (mins, "m")) if v) or "0m"


def render(results: list[ScenarioResult]) -> str:
    lines: list[str] = []
    for result in results:
        lines.append(
            f"\n## {result.scenario.name}"
            + (f" ({result.scenario.label.kind})" if result.scenario.label else "")
        )
        lines.append("| # | after | event | actor | new rule hits | score | band |")
        lines.append("|---|---|---|---|---|---|---|")
        for s in result.steps:
            hits = "<br>".join(s.new_hits) or "-"
            lines.append(
                f"| {s.number} | +{s.offset} | {s.event_type} | {s.actor} | {hits} | {s.score} | {s.band.value} |"
            )
        first = result.first_high
        lines.append(
            f"First reached High at step {first.number}, {first.event_type} (+{first.offset}); peak {result.peak.value}."
            if first
            else f"Never reached High; peak {result.peak.value}."
        )
    return "\n".join(lines)
