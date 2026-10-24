"""The Rule Set: versioned, analyst-editable detection config (ADR-0002).

Six rule kinds cover every pack:
- `sequence`: ordered steps on one Account inside a Window, optionally joined on a shared field.
- `first_sighting`: a value (device, country, ...) the Account has never used before.
- `velocity`: at least N matching events inside a Window.
- `shared_identifier`, `fraud_proximity`, `ring`: links between Accounts, evaluated over the Link Graph.
"""

from __future__ import annotations

import string
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from fraud_audit_assist.domain.durations import parse_duration
from fraud_audit_assist.domain.events import CORE_FIELDS, AttributeValue, Event


def _duration(value: Any) -> Any:
    return parse_duration(value) if isinstance(value, str) else value


Duration = Annotated[timedelta, BeforeValidator(_duration)]
RuleId = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")]


class RiskBand(StrEnum):
    """Ordered by severity. Comparisons are defined explicitly because str's own would order alphabetically."""

    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"

    @property
    def rank(self) -> int:
        return list(RiskBand).index(self)

    def __lt__(self, other: str) -> bool:
        return self.rank < RiskBand(other).rank

    def __le__(self, other: str) -> bool:
        return self.rank <= RiskBand(other).rank

    def __gt__(self, other: str) -> bool:
        return self.rank > RiskBand(other).rank

    def __ge__(self, other: str) -> bool:
        return self.rank >= RiskBand(other).rank


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _check_field_name(name: str) -> str:
    if name.startswith("attributes.") and len(name) > len("attributes."):
        return name
    if name in CORE_FIELDS:
        return name
    raise ValueError(
        f"Unknown field '{name}': use a core field ({', '.join(sorted(CORE_FIELDS))}) or attributes.<name>"
    )


FieldName = Annotated[str, BeforeValidator(_check_field_name)]


class Condition(_Strict):
    """A filter on one event field, so a step can say "a transfer of at least 1,000" rather than "a transfer"."""

    field: FieldName
    op: Literal["eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte", "exists"]
    value: AttributeValue | list[AttributeValue] | None = None

    @model_validator(mode="after")
    def _value_matches_op(self) -> Condition:
        if self.op == "exists":
            return self
        wants_list = self.op in ("in", "not_in")
        if wants_list != isinstance(self.value, list):
            raise ValueError(
                f"Condition on {self.field}: '{self.op}' needs {'a list' if wants_list else 'a single value'}"
            )
        if self.value is None:
            raise ValueError(f"Condition on {self.field}: '{self.op}' needs a value")
        return self

    def holds(self, event: Event) -> bool:
        actual = event.field(self.field)
        if self.op == "exists":
            return actual is not None and actual != ""
        if actual is None:
            return False
        match self.op:
            case "eq":
                return _same(actual, self.value)
            case "ne":
                return not _same(actual, self.value)
            case "in":
                return any(_same(actual, v) for v in self.value)  # type: ignore[union-attr]
            case "not_in":
                return not any(_same(actual, v) for v in self.value)  # type: ignore[union-attr]
        left, right = _number(actual), _number(self.value)
        if left is None or right is None:
            # A non-numeric value never satisfies a numeric comparison; failing closed avoids false hits.
            return False
        match self.op:
            case "gt":
                return left > right
            case "gte":
                return left >= right
            case "lt":
                return left < right
            case _:
                return left <= right


def _same(actual: AttributeValue, expected: object) -> bool:
    # Attributes arrive as strings from many sources, so "1000" and 1000 must compare equal.
    a, e = _number(actual), _number(expected)
    if a is not None and e is not None:
        return a == e
    return str(actual) == str(expected)


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


class Step(_Strict):
    """One step of a pattern: any of `types`, filtered by every condition in `where`."""

    types: list[str] = Field(min_length=1)
    where: list[Condition] = Field(default_factory=list)

    def matches(self, event: Event) -> bool:
        return event.type in self.types and all(c.holds(event) for c in self.where)


class _RuleBase(_Strict):
    id: RuleId
    name: str = Field(min_length=1)
    points: int = Field(gt=0, le=1000)
    explanation: str = Field(min_length=1)
    description: str = ""
    enabled: bool = True

    # Placeholders each kind fills in; checked at load so a typo fails at startup, not in an analyst's view.
    PLACEHOLDERS: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="after")
    def _known_placeholders(self) -> _RuleBase:
        used = {name for _, name, _, _ in string.Formatter().parse(self.explanation) if name}
        unknown = used - self.PLACEHOLDERS
        if unknown:
            allowed = ", ".join(sorted(self.PLACEHOLDERS)) or "none"
            raise ValueError(f"Rule {self.id}: unknown placeholder(s) {sorted(unknown)}; allowed: {allowed}")
        return self

    def explain(self, **values: object) -> str:
        return self.explanation.format_map({k: str(v) for k, v in values.items()})


class SequenceRule(_RuleBase):
    kind: Literal["sequence"]
    steps: list[Step] = Field(min_length=2, max_length=6)
    window: Duration
    join_on: list[FieldName] = Field(default_factory=list)

    PLACEHOLDERS: ClassVar[frozenset[str]] = frozenset({"elapsed", "window"})

    @model_validator(mode="after")
    def _positive_window(self) -> SequenceRule:
        if self.window <= timedelta(0):
            raise ValueError(f"Rule {self.id}: window must be positive")
        return self


class FirstSightingRule(_RuleBase):
    kind: Literal["first_sighting"]
    fields: list[FieldName] = Field(min_length=1)

    PLACEHOLDERS: ClassVar[frozenset[str]] = frozenset({"sighting"})


class VelocityRule(_RuleBase):
    kind: Literal["velocity"]
    step: Step
    count: int = Field(ge=2, le=1000)
    window: Duration

    PLACEHOLDERS: ClassVar[frozenset[str]] = frozenset({"count", "window", "types"})


class SharedIdentifierRule(_RuleBase):
    kind: Literal["shared_identifier"]
    identifier_kinds: list[str] = Field(min_length=1)
    distinct_customer: bool = True

    PLACEHOLDERS: ClassVar[frozenset[str]] = frozenset({"identifier", "value", "account"})


class FraudProximityRule(_RuleBase):
    kind: Literal["fraud_proximity"]
    identifier_kinds: list[str] = Field(min_length=1)

    PLACEHOLDERS: ClassVar[frozenset[str]] = frozenset({"identifier", "value", "account"})


class RingRule(_RuleBase):
    kind: Literal["ring"]
    identifier_kinds: list[str] = Field(min_length=1)
    account_threshold: int = Field(ge=2, le=1000)
    max_hops: int = Field(default=1, ge=1, le=4)

    PLACEHOLDERS: ClassVar[frozenset[str]] = frozenset(
        {"identifier", "value", "accounts", "threshold", "hops"}
    )


Rule = Annotated[
    SequenceRule | FirstSightingRule | VelocityRule | SharedIdentifierRule | FraudProximityRule | RingRule,
    Field(discriminator="kind"),
]
GraphRule = SharedIdentifierRule | FraudProximityRule | RingRule
GRAPH_RULE_TYPES = (SharedIdentifierRule, FraudProximityRule, RingRule)


class BandThresholds(_Strict):
    """Lowest Score of each band; anything below `medium` is Low."""

    medium: int = Field(gt=0)
    high: int
    critical: int

    @model_validator(mode="after")
    def _ascending(self) -> BandThresholds:
        if not self.medium < self.high < self.critical:
            raise ValueError("band thresholds must ascend: medium < high < critical")
        return self

    def band_for(self, score: int) -> RiskBand:
        if score >= self.critical:
            return RiskBand.CRITICAL
        if score >= self.high:
            return RiskBand.HIGH
        if score >= self.medium:
            return RiskBand.MEDIUM
        return RiskBand.LOW


class RuleSet(_Strict):
    """Everything detection is configured by. `version` is stamped on every Alert for audit."""

    version: str = Field(pattern=r"^[A-Za-z0-9._-]{1,32}$")
    risk_period: Duration = timedelta(days=7)
    bands: BandThresholds
    rules: list[Rule] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> RuleSet:
        ids = [r.id for r in self.rules]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate rule ids: {duplicates}")
        return self

    @property
    def active(self) -> list[Rule]:
        return [r for r in self.rules if r.enabled]

    @property
    def graph_rules(self) -> list[GraphRule]:
        return [r for r in self.active if isinstance(r, GRAPH_RULE_TYPES)]

    @property
    def uses_graph(self) -> bool:
        return bool(self.graph_rules)

    def rule(self, rule_id: str) -> Rule:
        for r in self.rules:
            if r.id == rule_id:
                return r
        raise KeyError(rule_id)

    @classmethod
    def load(cls, path: Path) -> RuleSet:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
