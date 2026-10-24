from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from fraud_audit_assist.domain.packs import load_pack
from fraud_audit_assist.simulator.personas import (
    PersonaFile,
    generate_dataset,
    load_personas,
    read_dataset,
    write_dataset,
)
from tests.conftest import PACKS_DIR


@pytest.mark.parametrize("pack_id", ["investor_portal", "digital_banking"])
def test_same_seed_gives_a_byte_identical_dataset(pack_id: str, tmp_path: Path) -> None:
    pack = load_pack(PACKS_DIR / pack_id).pack
    personas = load_personas(PACKS_DIR / pack_id, pack)
    write_dataset(generate_dataset(personas, pack, 7, 200), tmp_path / "a.jsonl")
    write_dataset(generate_dataset(personas, pack, 7, 200), tmp_path / "b.jsonl")
    write_dataset(generate_dataset(personas, pack, 8, 200), tmp_path / "c.jsonl")
    assert (tmp_path / "a.jsonl").read_bytes() == (tmp_path / "b.jsonl").read_bytes()
    assert (tmp_path / "a.jsonl").read_bytes() != (tmp_path / "c.jsonl").read_bytes()
    assert len(read_dataset(tmp_path / "a.jsonl")) == 200


def test_every_persona_appears_even_in_a_small_dataset() -> None:
    pack = load_pack(PACKS_DIR / "investor_portal").pack
    personas = load_personas(PACKS_DIR / "investor_portal", pack)
    counts = Counter(a.label.persona for a in generate_dataset(personas, pack, 1, 60))
    assert set(counts) == {p.id for p in personas.personas}
    assert counts["fraud_ring"] == 5


def test_ring_members_share_their_identifiers() -> None:
    pack = load_pack(PACKS_DIR / "investor_portal").pack
    dataset = generate_dataset(load_personas(PACKS_DIR / "investor_portal", pack), pack, 3, 100)
    ring = [a for a in dataset if a.label.persona == "fraud_ring"]
    banks = {
        e.attributes["new_bank_account_hash"]
        for a in ring
        for e in a.events
        if e.type == "BankDetailsChanged"
    }
    assert len(banks) == 1


def test_vars_are_drawn_once_per_account() -> None:
    pack = load_pack(PACKS_DIR / "digital_banking").pack
    dataset = generate_dataset(load_personas(PACKS_DIR / "digital_banking", pack), pack, 5, 100)
    for account in (a for a in dataset if a.label.persona == "sim_swap_takeover"):
        payees = {
            e.attributes["payee_account_hash"]
            for e in account.events
            if e.type in ("PayeeAdded", "TransferInitiated")
        }
        assert len(payees) == 1


def test_slow_burn_steps_wait_out_their_pairwise_windows() -> None:
    pack = load_pack(PACKS_DIR / "investor_portal").pack
    dataset = generate_dataset(load_personas(PACKS_DIR / "investor_portal", pack), pack, 11, 100)
    [slow] = [a for a in dataset if a.label.persona == "slow_burn_fraudster"]
    at = {e.type: e.occurred_at for e in slow.events if e.type != "LoginSucceeded"}
    assert (at["MfaEnrolled"] - at["PasswordReset"]).total_seconds() > 3600
    assert (at["EmailChanged"] - at["PasswordReset"]).days >= 1
    assert (at["BankDetailsChanged"] - at["EmailChanged"]).total_seconds() > 3 * 86400
    assert slow.label.tags == ["evasion"]


def test_persona_file_errors_name_the_problem() -> None:
    pack = load_pack(PACKS_DIR / "investor_portal").pack
    data = {
        "clock": {"start": "2026-01-01T00:00:00Z", "span": "90d", "journey_start": ["68d", "74d"]},
        "home_country": "GB",
        "personas": [
            {"id": "filler", "label": "Genuine", "fill": True, "baseline": {"gap": ["1d", "2d"]}},
            {"id": "bad", "label": "Shoplifting", "journey": [{"type": "Teleported", "as": "ghost"}]},
        ],
    }
    with pytest.raises(ValueError, match="Shoplifting") as error:
        PersonaFile.model_validate(data).check(pack)
    assert "Teleported" in str(error.value)
    assert "ghost" in str(error.value)


def test_too_few_accounts_for_the_minimums_is_an_error() -> None:
    pack = load_pack(PACKS_DIR / "investor_portal").pack
    with pytest.raises(ValueError, match="too few"):
        generate_dataset(load_personas(PACKS_DIR / "investor_portal", pack), pack, 1, 10)
