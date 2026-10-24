"""Seeded synthetic Datasets from a pack's declarative Personas (`personas.yaml`).

A Persona is a template of behaviour: a baseline of ordinary activity, optional named actors (a fraudster's device,
a ring's shared bank account) and a journey of steps with randomised gaps. Every random choice comes from one seeded
generator, so the same pack, seed and size always give a byte-identical Dataset.

Values in actors and attributes may use placeholders:
    {n}        the account number, zero-padded       {hex}      the account number in hex
    {group}    the ring/group number                  {int:A-B}  a random integer from A to B (zero-padded to B)
    {hex4}     four random hex digits                 {choice:a|b|c}  one of the options
    {var:name} a Persona `vars` value, drawn once per Account, so two steps can share one random payee
"""

from __future__ import annotations

import random
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from fraud_audit_assist.domain.durations import parse_duration
from fraud_audit_assist.domain.events import AwareDatetime, Event
from fraud_audit_assist.domain.packs import DomainPack
from fraud_audit_assist.domain.scenarios import GENUINE, deterministic_event_id

_PLACEHOLDER = re.compile(r"\{([a-z0-9]+)(?::([^}]*))?\}")


def _duration(value: Any) -> Any:
    return parse_duration(value) if isinstance(value, str) else value


Duration = Annotated[timedelta, BeforeValidator(_duration)]


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Gap(_Strict):
    """A random gap in whole minutes between `min` and `max`, inclusive."""

    min: Duration
    max: Duration

    @model_validator(mode="before")
    @classmethod
    def _from_pair(cls, value: Any) -> Any:
        if isinstance(value, list | tuple) and len(value) == 2:
            return {"min": value[0], "max": value[1]}
        if isinstance(value, str):
            return {"min": value, "max": value}
        return value

    @model_validator(mode="after")
    def _ordered(self) -> Gap:
        if self.max < self.min:
            raise ValueError("gap max must not be below min")
        return self

    def draw(self, rng: random.Random) -> timedelta:
        low, high = int(self.min.total_seconds() // 60), int(self.max.total_seconds() // 60)
        return timedelta(minutes=rng.randint(low, high))


class ActorTemplate(_Strict):
    device: str | None = None
    ip: str | None = None
    country: str | None = None
    channel: str = "web"


class StepTemplate(_Strict):
    type: str
    actor: str = Field(default="holder", alias="as")
    after: Gap = Gap(min=timedelta(0), max=timedelta(0))
    # 1-based index of the earlier step the gap is measured from; default is the step just before.
    from_step: int | None = Field(default=None, alias="from", ge=1)
    attributes: dict[str, str] = Field(default_factory=dict)


class Baseline(_Strict):
    gap: Gap
    # "journey" stops ordinary logins when the journey starts; "end" keeps them going to the end of the clock.
    until: str = Field(default="journey", pattern="^(journey|end)$")


class PerLogin(_Strict):
    """A step that may follow each baseline login, such as an active trader's sale."""

    chance: float = Field(gt=0, le=1)
    step: StepTemplate


class Persona(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    label: str
    tags: list[str] = Field(default_factory=list)
    share: float = Field(default=0.0, ge=0, le=1)
    min_count: int = Field(default=0, ge=0)
    fill: bool = False
    group_size: int = Field(default=1, ge=1, le=100)
    baseline: Baseline | None = None
    per_login: PerLogin | None = None
    vars: dict[str, str] = Field(default_factory=dict)
    actors: dict[str, ActorTemplate] = Field(default_factory=dict)
    journey: list[StepTemplate] = Field(default_factory=list)


class Clock(_Strict):
    start: AwareDatetime
    span: Duration
    journey_start: Gap


class PersonaFile(_Strict):
    clock: Clock
    home_country: str = Field(min_length=2, max_length=2)
    personas: list[Persona] = Field(min_length=1)

    @model_validator(mode="after")
    def _one_fill(self) -> PersonaFile:
        if sum(p.fill for p in self.personas) != 1:
            raise ValueError("exactly one persona must have fill: true")
        return self

    def check(self, pack: DomainPack) -> None:
        typologies = {t.id for t in pack.typologies}
        problems = []
        for persona in self.personas:
            if persona.label != GENUINE and persona.label not in typologies:
                problems.append(f"{persona.id}: label '{persona.label}' is not a typology or {GENUINE}")
            steps = [*persona.journey, *([persona.per_login.step] if persona.per_login else [])]
            for number, step in enumerate(steps, start=1):
                if step.type not in pack.event_type_names:
                    problems.append(f"{persona.id} step {number}: unknown event type '{step.type}'")
                if step.actor != "holder" and step.actor not in persona.actors:
                    problems.append(f"{persona.id} step {number}: unknown actor '{step.actor}'")
                if step.actor == "holder" and persona.baseline is None:
                    problems.append(f"{persona.id} step {number}: no baseline, so the step needs an actor")
                if step.from_step is not None and step.from_step >= number:
                    problems.append(f"{persona.id} step {number}: 'from' must name an earlier step")
        if problems:
            raise ValueError("; ".join(problems))


class DatasetLabel(BaseModel):
    persona: str
    kind: str
    tags: list[str] = Field(default_factory=list)

    @property
    def is_fraud(self) -> bool:
        return self.kind != GENUINE


class LabelledAccount(BaseModel):
    """One generated Account. The Label is ground truth for evaluation; it is never sent to the API."""

    account_id: str
    label: DatasetLabel
    events: list[Event]


def load_personas(pack_dir: Path, pack: DomainPack) -> PersonaFile:
    personas = PersonaFile.model_validate(
        yaml.safe_load((pack_dir / "personas.yaml").read_text(encoding="utf-8"))
    )
    personas.check(pack)
    return personas


def generate_dataset(
    personas: PersonaFile, pack: DomainPack, seed: int, accounts: int
) -> list[LabelledAccount]:
    # A seeded PRNG is the point: synthetic data must be reproducible, and nothing here is a secret.
    rng = random.Random(seed)  # nosec B311
    slots = _slots(personas, accounts)
    rng.shuffle(slots)
    name = f"{pack.id}-{seed}-{accounts}"
    return [
        _AccountBuilder(name, number, rng, personas, pack.id).build(persona, group)
        for number, (persona, group) in enumerate(slots, start=1)
    ]


def _slots(personas: PersonaFile, accounts: int) -> list[tuple[Persona, int]]:
    slots: list[tuple[Persona, int]] = []
    group_counter = 0
    for persona in personas.personas:
        if persona.fill:
            continue
        count = max(persona.min_count, round(accounts * persona.share))
        groups = max(1, round(count / persona.group_size)) if count else 0
        for _ in range(groups if persona.group_size > 1 else count):
            group_counter += 1
            slots.extend((persona, group_counter) for _ in range(persona.group_size))
    filler = next(p for p in personas.personas if p.fill)
    if len(slots) >= accounts:
        raise ValueError(
            f"{accounts} accounts is too few for the personas' minimum counts ({len(slots)} needed)"
        )
    slots.extend((filler, 0) for _ in range(accounts - len(slots)))
    return slots


class _AccountBuilder:
    def __init__(
        self, dataset: str, number: int, rng: random.Random, personas: PersonaFile, pack_id: str
    ) -> None:
        self._dataset = dataset
        self._number = number
        self._rng = rng
        self._personas = personas
        self._group = 0
        self._vars: dict[str, str] = {}
        self._events: list[Event] = []
        prefix = pack_id[:3].upper()
        self.account_id = f"{prefix}-ACC-{number:05d}"
        self.customer_id = f"{prefix}-CUST-{number:05d}"
        self._usual = ActorTemplate(
            device=f"D-{number:05d}", ip=f"2001:db8::{number:x}", country=personas.home_country
        )

    def build(self, persona: Persona, group: int) -> LabelledAccount:
        self._group = group
        self._vars = {name: self._render(value) for name, value in sorted(persona.vars.items())}
        clock = self._personas.clock
        end = clock.start + clock.span
        journey_start = clock.start + clock.journey_start.draw(self._rng)
        actors = {"holder": self._usual, **{name: self._actor(a) for name, a in persona.actors.items()}}

        if persona.baseline is not None:
            until = end if persona.baseline.until == "end" or not persona.journey else journey_start
            at = clock.start + persona.baseline.gap.draw(self._rng)
            while at < until:
                self._add("LoginSucceeded", at, actors["holder"], {})
                if persona.per_login and self._rng.random() < persona.per_login.chance:
                    step = persona.per_login.step
                    follow = at + step.after.draw(self._rng)
                    # Every event must stay on the clock, so a follow-up that would run past its end is dropped.
                    if follow <= end:
                        self._add(step.type, follow, actors[step.actor], self._attributes(step))
                at += persona.baseline.gap.draw(self._rng)

        times: list[datetime] = []
        for index, step in enumerate(persona.journey):
            anchor = journey_start if index == 0 else times[(step.from_step or index) - 1]
            at = anchor + step.after.draw(self._rng)
            times.append(at)
            self._add(step.type, at, actors[step.actor], self._attributes(step))

        events = sorted(self._events, key=lambda e: (e.occurred_at, str(e.event_id)))
        return LabelledAccount(
            account_id=self.account_id,
            label=DatasetLabel(persona=persona.id, kind=persona.label, tags=persona.tags),
            events=events,
        )

    def _actor(self, template: ActorTemplate) -> ActorTemplate:
        def value(text: str | None, usual: str | None) -> str | None:
            if text == "usual":
                return usual
            return self._render(text) if text else None

        return ActorTemplate(
            device=value(template.device, self._usual.device),
            ip=value(template.ip, self._usual.ip),
            country=value(template.country, self._usual.country),
            channel=template.channel,
        )

    def _attributes(self, step: StepTemplate) -> dict[str, str]:
        return {key: self._render(value) for key, value in sorted(step.attributes.items())}

    def _render(self, text: str) -> str:
        def replace(match: re.Match[str]) -> str:
            name, argument = match.group(1), match.group(2)
            match name:
                case "n":
                    return f"{self._number:05d}"
                case "hex":
                    return f"{self._number:x}"
                case "group":
                    return str(self._group)
                case "hex4":
                    return f"{self._rng.randrange(0x10000):04x}"
                case "int":
                    low, high = (argument or "0-9").split("-")
                    width = len(high) if low.startswith("0") and len(low) > 1 else 0
                    return f"{self._rng.randint(int(low), int(high)):0{width}d}"
                case "choice":
                    options: list[str] = (argument or "").split("|")
                    return options[self._rng.randrange(len(options))]
                case "var":
                    if argument not in self._vars:
                        raise ValueError(f"persona template uses undefined var '{argument}'")
                    return self._vars[argument]
            raise ValueError(f"unknown placeholder '{{{name}}}' in persona template")

        return _PLACEHOLDER.sub(replace, text)

    def _add(self, type_: str, at: datetime, actor: ActorTemplate, attributes: dict[str, str]) -> None:
        self._events.append(
            Event(
                event_id=deterministic_event_id(self._dataset, self.account_id, str(len(self._events) + 1)),
                account_id=self.account_id,
                customer_id=self.customer_id,
                type=type_,
                occurred_at=at,
                device_id=actor.device,
                ip_address=actor.ip,
                country=actor.country,
                channel=actor.channel,
                attributes=dict(attributes),
            )
        )


def write_dataset(accounts: list[LabelledAccount], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # A fixed "\n" so the same seed is byte-identical on every OS.
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for account in accounts:
            file.write(account.model_dump_json() + "\n")


def read_dataset(path: Path) -> list[LabelledAccount]:
    with path.open(encoding="utf-8") as file:
        return [LabelledAccount.model_validate_json(line) for line in file if line.strip()]
