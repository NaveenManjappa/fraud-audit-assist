"""Scenarios: hand-written Account journeys in YAML or JSON, expanded into events.

A Scenario is one fixed script with relative timings, so a new journey needs no code. The same format is used by
the simulator, dry runs and the golden evaluation dataset.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from uuid import NAMESPACE_URL, UUID, uuid5

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, model_validator

from fraud_audit_assist.domain.durations import parse_duration
from fraud_audit_assist.domain.events import AttributeValue, AwareDatetime, Event
from fraud_audit_assist.domain.packs import DomainPack

GENUINE = "Genuine"
HOLDER_ACTOR = "holder"

# Every journey starts here unless its file says otherwise, so runs are reproducible (Mon 2026-09-21 09:00 UTC).
DEFAULT_START = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)

_EVENT_NAMESPACE = uuid5(NAMESPACE_URL, "https://github.com/NaveenManjappa/fraud-audit-assist/events")


def deterministic_event_id(*parts: str) -> UUID:
    """A stable id, so posting a Scenario twice exercises idempotency instead of creating duplicates."""
    return uuid5(_EVENT_NAMESPACE, "/".join(parts))


def _offset(value: Any) -> Any:
    return parse_duration(value) if isinstance(value, str) else value


Offset = Annotated[timedelta, BeforeValidator(_offset)]


class _Strict(BaseModel):
    # A misspelt field would otherwise be dropped silently and the step would run with a default.
    model_config = ConfigDict(frozen=True, extra="forbid")


class Actor(_Strict):
    device: str | None = None
    ip: str | None = None
    country: str | None = Field(default=None, min_length=2, max_length=2)
    channel: str = "web"


class Baseline(Actor):
    """The customer's usual login, repeated weekly before the journey so a First Sighting has history."""

    weeks: int = Field(default=13, ge=1, le=104)


class ScenarioLabel(_Strict):
    kind: str
    intent: str = Field(min_length=1)

    @model_validator(mode="after")
    def _one_line(self) -> ScenarioLabel:
        if "\n" in self.intent:
            raise ValueError("label intent must be one line")
        return self

    @property
    def is_fraud(self) -> bool:
        return self.kind != GENUINE


class ScenarioStep(_Strict):
    after: Offset
    type: str
    actor: str = Field(default=HOLDER_ACTOR, alias="as")
    attributes: dict[str, AttributeValue] = Field(default_factory=dict)


class Scenario(_Strict):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    description: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    customer_id: str | None = None
    start: AwareDatetime = DEFAULT_START
    baseline: Baseline | None = None
    actors: dict[str, Actor] = Field(default_factory=dict)
    steps: list[ScenarioStep] = Field(min_length=1)
    label: ScenarioLabel | None = None
    confirmed_fraud: AwareDatetime | None = None

    @property
    def customer(self) -> str:
        return self.customer_id or f"CUST-{self.account_id}"

    def check(self, pack: DomainPack) -> None:
        """Fails, naming the step, on an event type, actor or label the pack does not know."""
        problems: list[str] = []
        for number, step in enumerate(self.steps, start=1):
            if step.type not in pack.event_type_names:
                problems.append(f"step {number}: unknown event type '{step.type}'")
            if step.actor not in self._all_actors():
                hint = (
                    "no baseline, so the step needs an 'as' actor"
                    if step.actor == HOLDER_ACTOR
                    else "unknown actor"
                )
                problems.append(f"step {number}: {hint} '{step.actor}'")
        if (
            self.label
            and self.label.kind != GENUINE
            and self.label.kind not in {t.id for t in pack.typologies}
        ):
            problems.append(f"label kind '{self.label.kind}' is not a typology of {pack.id} or {GENUINE}")
        if problems:
            raise ValueError(f"Scenario {self.name}: " + "; ".join(problems))

    def events(self) -> list[Event]:
        """The baseline logins, then the steps, as events with stable ids."""
        events: list[Event] = []
        if self.baseline is not None:
            for week in range(1, self.baseline.weeks + 1):
                events.append(
                    Event(
                        event_id=deterministic_event_id(self.name, "baseline", str(week)),
                        account_id=self.account_id,
                        customer_id=self.customer,
                        type="LoginSucceeded",
                        occurred_at=self.start - timedelta(days=7 * (self.baseline.weeks + 1 - week)),
                        device_id=self.baseline.device,
                        ip_address=self.baseline.ip,
                        country=self.baseline.country,
                        channel=self.baseline.channel,
                    )
                )
        actors = self._all_actors()
        for number, step in enumerate(self.steps, start=1):
            actor = actors[step.actor]
            events.append(
                Event(
                    event_id=deterministic_event_id(self.name, str(number)),
                    account_id=self.account_id,
                    customer_id=self.customer,
                    type=step.type,
                    occurred_at=self.start + step.after,
                    device_id=actor.device,
                    ip_address=actor.ip,
                    country=actor.country,
                    channel=actor.channel,
                    attributes=step.attributes,
                )
            )
        return events

    def step_events(self) -> list[Event]:
        return self.events()[self.baseline.weeks if self.baseline else 0 :]

    def first_event_at(self) -> datetime:
        return min(e.occurred_at for e in self.events())

    def _all_actors(self) -> dict[str, Actor]:
        actors = dict(self.actors)
        if self.baseline is not None:
            actors.setdefault(HOLDER_ACTOR, self.baseline)
        return actors


def parse_scenario(data: dict[str, Any], source: str, pack: DomainPack) -> Scenario:
    try:
        scenario = Scenario.model_validate(data)
    except ValidationError as error:
        raise ValueError(f"{source}: {error}") from error
    scenario.check(pack)
    return scenario


def load_scenario(path: Path, pack: DomainPack) -> Scenario:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    return parse_scenario(data, str(path), pack)


def load_scenarios(folder: Path, pack: DomainPack) -> list[Scenario]:
    """Every Scenario in a folder, ordered by first event so history others link to is replayed first.

    Ties keep file-name order, so every machine replays the same sequence.
    """
    files = sorted(p for p in folder.iterdir() if p.suffix in (".yaml", ".yml", ".json"))
    scenarios = [load_scenario(p, pack) for p in files]
    return sorted(scenarios, key=lambda s: s.first_event_at())
