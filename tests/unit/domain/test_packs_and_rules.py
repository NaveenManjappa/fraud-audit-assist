from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from fraud_audit_assist.domain.durations import format_duration, parse_duration
from fraud_audit_assist.domain.packs import available_rule_versions, load_pack
from fraud_audit_assist.domain.rules import RuleSet
from fraud_audit_assist.domain.scenarios import parse_scenario
from tests.conftest import PACKS_DIR


def ruleset(rule: dict[str, Any]) -> dict[str, Any]:
    return {"version": "t", "bands": {"medium": 1, "high": 2, "critical": 3}, "rules": [rule]}


@pytest.mark.parametrize("pack_id", ["investor_portal", "digital_banking"])
def test_every_shipped_rule_version_loads_against_its_pack(pack_id: str) -> None:
    versions = available_rule_versions(PACKS_DIR / pack_id)
    assert versions
    for version in versions:
        assert load_pack(PACKS_DIR / pack_id, version).rules.version == version


@pytest.mark.parametrize(
    ("rule", "message"),
    [
        (
            {
                "id": "X",
                "name": "x",
                "kind": "sequence",
                "steps": [{"types": ["A"]}],
                "window": "1h",
                "points": 1,
                "explanation": "e",
            },
            "at least 2",
        ),
        (
            {
                "id": "X",
                "name": "x",
                "kind": "sequence",
                "steps": [{"types": ["A"]}, {"types": ["B"]}],
                "window": "0m",
                "points": 1,
                "explanation": "e",
            },
            "positive",
        ),
        (
            {
                "id": "X",
                "name": "x",
                "kind": "first_sighting",
                "fields": ["device_id"],
                "points": 1,
                "explanation": "{oops}",
            },
            "unknown placeholder",
        ),
        (
            {
                "id": "X",
                "name": "x",
                "kind": "first_sighting",
                "fields": ["shoe_size"],
                "points": 1,
                "explanation": "e",
            },
            "Unknown field",
        ),
        (
            {
                "id": "X",
                "name": "x",
                "kind": "ring",
                "identifier_kinds": ["device"],
                "account_threshold": 1,
                "points": 1,
                "explanation": "e",
            },
            "greater than or equal to 2",
        ),
        (
            {
                "id": "X",
                "name": "x",
                "kind": "sequence",
                "steps": [
                    {"types": ["A"], "where": [{"field": "channel", "op": "in", "value": "web"}]},
                    {"types": ["B"]},
                ],
                "window": "1h",
                "points": 1,
                "explanation": "e",
            },
            "needs a list",
        ),
    ],
)
def test_malformed_rules_fail_at_load(rule: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        RuleSet.model_validate(ruleset(rule))


def test_rules_naming_unknown_event_types_fail_against_the_pack() -> None:
    pack = load_pack(PACKS_DIR / "investor_portal").pack
    rules = RuleSet.model_validate(
        ruleset(
            {
                "id": "X",
                "name": "x",
                "kind": "sequence",
                "steps": [{"types": ["PasswordReset"]}, {"types": ["Teleported"]}],
                "window": "1h",
                "points": 1,
                "explanation": "e",
            }
        )
    )
    with pytest.raises(ValueError, match="unknown event types \\['Teleported'\\]"):
        pack.check_rules(rules)


def test_graph_rules_may_only_use_linking_identifiers() -> None:
    pack = load_pack(PACKS_DIR / "investor_portal").pack
    rules = RuleSet.model_validate(
        ruleset(
            {
                "id": "G",
                "name": "g",
                "kind": "fraud_proximity",
                "identifier_kinds": ["ip_address"],
                "points": 1,
                "explanation": "e",
            }
        )
    )
    with pytest.raises(ValueError, match="not linking identifiers"):
        pack.check_rules(rules)


def test_bands_must_ascend() -> None:
    with pytest.raises(ValidationError, match="ascend"):
        RuleSet.model_validate(
            {"version": "t", "bands": {"medium": 5, "high": 5, "critical": 9}, "rules": []}
        )


@pytest.mark.parametrize(
    ("text", "expected"),
    [("10m", timedelta(minutes=10)), ("1d5h", timedelta(days=1, hours=5)), ("90s", timedelta(seconds=90))],
)
def test_parse_duration(text: str, expected: timedelta) -> None:
    assert parse_duration(text) == expected


@pytest.mark.parametrize("text", ["", "5", "1h1d", "1x", "1h1h"])
def test_parse_duration_rejects_bad_text(text: str) -> None:
    with pytest.raises(ValueError, match="Invalid duration"):
        parse_duration(text)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (timedelta(minutes=4), "4 min"),
        (timedelta(hours=26, minutes=30), "26h 30 min"),
        (timedelta(days=10, hours=6), "10d 6h"),
        (timedelta(days=7), "7d"),
    ],
)
def test_format_duration(value: timedelta, expected: str) -> None:
    assert format_duration(value) == expected


def test_scenario_names_the_step_with_an_unknown_event_type() -> None:
    pack = load_pack(PACKS_DIR / "investor_portal").pack
    data = {
        "name": "bad",
        "description": "d",
        "account_id": "A",
        "baseline": {"device": "D"},
        "steps": [{"after": "0m", "type": "Teleported"}],
    }
    with pytest.raises(ValueError, match="step 1: unknown event type 'Teleported'"):
        parse_scenario(data, "bad.yaml", pack)


def test_scenario_rejects_misspelt_fields() -> None:
    pack = load_pack(PACKS_DIR / "investor_portal").pack
    data = {
        "name": "bad",
        "description": "d",
        "account_id": "A",
        "steps": [{"after": "0m", "type": "LoginSucceeded", "atributes": {}}],
    }
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        parse_scenario(data, "bad.yaml", pack)


def test_scenario_event_ids_are_stable() -> None:
    pack = load_pack(PACKS_DIR / "investor_portal").pack
    data = {
        "name": "same",
        "description": "d",
        "account_id": "A",
        "baseline": {"device": "D"},
        "steps": [{"after": "0m", "type": "LoginSucceeded"}],
    }
    first = parse_scenario(data, "a", pack).events()
    second = parse_scenario(data, "a", pack).events()
    assert [e.event_id for e in first] == [e.event_id for e in second]
    assert len(first) == 14
