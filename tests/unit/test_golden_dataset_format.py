"""Keeps the golden dataset well-formed. It checks shape only; scoring against it is the future evaluation's job."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fraud_audit_assist.domain.packs import load_pack
from fraud_audit_assist.domain.rules import RiskBand
from fraud_audit_assist.domain.scenarios import GENUINE, parse_scenario
from tests.conftest import PACKS_DIR, REPO_ROOT

GOLDEN = sorted((REPO_ROOT / "evals" / "golden").glob("*.jsonl"))


def cases(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_golden_files_exist() -> None:
    assert {p.name for p in GOLDEN} == {"investor_portal.v1.jsonl", "digital_banking.v1.jsonl"}


@pytest.mark.parametrize("path", GOLDEN, ids=lambda p: p.name)
def test_every_case_is_valid_against_its_pack(path: Path) -> None:
    ids: set[str] = set()
    for case in cases(path):
        assert case["id"] not in ids, f"duplicate id {case['id']}"
        ids.add(case["id"])
        loaded = load_pack(PACKS_DIR / case["pack"], case["rules_version"])
        pack = loaded.pack
        for scenario in [*case["context"], case["target"]]:
            parse_scenario(scenario, f"{case['id']}/{scenario['name']}", pack)

        assert case["label"]["kind"] in {GENUINE, *(t.id for t in pack.typologies)}
        detection = case["expected"]["detection"]
        assert RiskBand(detection["peak_band"]) is loaded.rules.bands.band_for(detection["peak_score"])
        assert detection["flagged"] == (detection["first_high_step"] is not None)
        known_rules = {r.id for r in loaded.rules.rules}
        assert set(detection["rules_hit"]) <= known_rules

        investigation = case["expected"]["investigation"]
        if investigation is None:
            continue
        assert set(investigation["acceptable_verdicts"]) <= set(pack.verdicts)
        assert set(investigation["acceptable_recommendations"]) <= {a.id for a in pack.actions}
        assert set(investigation["must_cite_event_types"]) <= pack.event_type_names
        assert investigation["case_expected"] == detection["flagged"]
