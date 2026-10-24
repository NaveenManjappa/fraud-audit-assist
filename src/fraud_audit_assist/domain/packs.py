"""Domain Packs: everything that makes the engine about one business rather than another (ADR-0017).

A pack is a directory:

    packs/<id>/pack.yaml        event catalogue, identifiers, typologies, actions, terminology
    packs/<id>/rules/vN.yaml    versioned Rule Sets
    packs/<id>/personas.yaml    synthetic behaviour templates for the simulator
    packs/<id>/scenarios/*.yaml hand-written journeys

The engine, agents and API read only the pack's models, so a new business is a new directory, not new code.
"""

from __future__ import annotations

import re
from functools import cached_property
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from fraud_audit_assist.domain.events import Event
from fraud_audit_assist.domain.rules import (
    GRAPH_RULE_TYPES,
    FirstSightingRule,
    RuleSet,
    SequenceRule,
    VelocityRule,
)

INCONCLUSIVE = "Inconclusive"
LIKELY_GENUINE = "LikelyGenuine"
_PASCAL = r"^[A-Z][A-Za-z0-9]{1,63}$"


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EventTypeSpec(_Strict):
    name: str = Field(pattern=_PASCAL)
    category: str = Field(min_length=1)
    description: str = ""


class IdentifierSpec(_Strict):
    """Where an identifier is read from, and whether reusing it across Accounts suggests one actor."""

    kind: str = Field(pattern=r"^[a-z][a-z0-9_]{1,31}$")
    field: str = Field(min_length=1)
    label: str = Field(min_length=1)
    linking: bool
    description: str = ""

    @property
    def pseudonym_prefix(self) -> str:
        return self.kind.upper()

    def value_in(self, event: Event) -> str | None:
        value = event.field(self.field)
        if value is None or value == "":
            return None
        return str(value)


class TypologySpec(_Strict):
    id: str = Field(pattern=_PASCAL)
    description: str = Field(min_length=1)


class ActionSpec(_Strict):
    """An action an analyst may approve. `restrictive` actions limit the customer and need a fraud verdict."""

    id: str = Field(pattern=_PASCAL)
    description: str = Field(min_length=1)
    restrictive: bool
    duration_days: int | None = Field(default=None, ge=1, le=365)
    # False for "close with no action": there is nothing for the source system to enforce.
    records_intervention: bool = True


class Terminology(_Strict):
    account: str = "Account"
    customer: str = "Customer"


class DomainPack(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    name: str
    description: str = Field(min_length=20)
    terminology: Terminology = Terminology()
    event_types: list[EventTypeSpec] = Field(min_length=1)
    identifiers: list[IdentifierSpec] = Field(min_length=1)
    non_identifying_attributes: list[str] = Field(default_factory=list)
    typologies: list[TypologySpec] = Field(min_length=1)
    actions: list[ActionSpec] = Field(min_length=2)
    default_rules: str = Field(pattern=r"^v\d+$")

    @model_validator(mode="after")
    def _consistent(self) -> DomainPack:
        for label, values in (
            ("event type", [e.name for e in self.event_types]),
            ("identifier kind", [i.kind for i in self.identifiers]),
            ("typology", [t.id for t in self.typologies]),
            ("action", [a.id for a in self.actions]),
        ):
            duplicates = sorted({v for v in values if values.count(v) > 1})
            if duplicates:
                raise ValueError(f"duplicate {label}s: {duplicates}")
        reserved = {INCONCLUSIVE, LIKELY_GENUINE} & {t.id for t in self.typologies}
        if reserved:
            raise ValueError(f"typologies may not use the reserved verdicts {sorted(reserved)}")
        if not any(not a.restrictive for a in self.actions):
            # A genuine verdict must always have somewhere safe to land.
            raise ValueError("a pack needs at least one non-restrictive action")
        return self

    @cached_property
    def event_type_names(self) -> frozenset[str]:
        return frozenset(e.name for e in self.event_types)

    @cached_property
    def verdicts(self) -> list[str]:
        return [t.id for t in self.typologies] + [INCONCLUSIVE, LIKELY_GENUINE]

    @cached_property
    def linking_identifiers(self) -> list[IdentifierSpec]:
        return [i for i in self.identifiers if i.linking]

    def identifier(self, kind: str) -> IdentifierSpec:
        for spec in self.identifiers:
            if spec.kind == kind:
                return spec
        raise KeyError(kind)

    def action(self, action_id: str) -> ActionSpec:
        for spec in self.actions:
            if spec.id == action_id:
                return spec
        raise KeyError(action_id)

    def check_rules(self, rules: RuleSet) -> None:
        """Fails when a Rule names an event type or identifier the pack does not define."""
        problems: list[str] = []
        for rule in rules.rules:
            steps = []
            if isinstance(rule, SequenceRule):
                steps = rule.steps
            elif isinstance(rule, VelocityRule):
                steps = [rule.step]
            for step in steps:
                unknown = sorted(set(step.types) - self.event_type_names)
                if unknown:
                    problems.append(f"Rule {rule.id}: unknown event types {unknown}")
            if isinstance(rule, FirstSightingRule) and not rule.fields:
                problems.append(f"Rule {rule.id}: no fields")
            if isinstance(rule, GRAPH_RULE_TYPES):
                linking = {i.kind for i in self.linking_identifiers}
                bad = sorted(set(rule.identifier_kinds) - linking)
                if bad:
                    problems.append(f"Rule {rule.id}: {bad} are not linking identifiers of pack {self.id}")
        if problems:
            raise ValueError("; ".join(problems))


class LoadedPack(BaseModel):
    """A pack with one of its Rule Sets, checked against each other."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    pack: DomainPack
    rules: RuleSet
    directory: Path


_RULES_FILE = re.compile(r"^(v\d+)\.ya?ml$")


def available_rule_versions(pack_dir: Path) -> list[str]:
    versions = [m.group(1) for p in (pack_dir / "rules").glob("*.y*ml") if (m := _RULES_FILE.match(p.name))]
    return sorted(versions, key=lambda v: int(v[1:]))


def load_pack(pack_dir: Path, rules_version: str | None = None) -> LoadedPack:
    """Loads and cross-checks a pack and one Rule Set (its default unless named). Fails fast on any mismatch."""
    pack_file = pack_dir / "pack.yaml"
    if not pack_file.is_file():
        raise FileNotFoundError(f"No pack.yaml in {pack_dir}")
    pack = DomainPack.model_validate(yaml.safe_load(pack_file.read_text(encoding="utf-8")))
    if pack.id != pack_dir.name:
        raise ValueError(f"Pack id '{pack.id}' must match its directory name '{pack_dir.name}'")

    version = rules_version or pack.default_rules
    candidates = [pack_dir / "rules" / f"{version}.yaml", pack_dir / "rules" / f"{version}.yml"]
    rules_file = next((c for c in candidates if c.is_file()), None)
    if rules_file is None:
        raise FileNotFoundError(
            f"Pack {pack.id} has no rules {version}; available: {available_rule_versions(pack_dir)}"
        )
    rules = RuleSet.load(rules_file)
    if rules.version != version:
        raise ValueError(f"{rules_file} declares version '{rules.version}', expected '{version}'")
    pack.check_rules(rules)
    return LoadedPack(pack=pack, rules=rules, directory=pack_dir)
