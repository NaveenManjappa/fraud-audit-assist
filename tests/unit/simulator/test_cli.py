from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from fraud_audit_assist.cli import app
from tests.conftest import PACKS_DIR

runner = CliRunner()


def test_dry_run_shows_margaret_reaching_high_before_the_bank_change() -> None:
    result = runner.invoke(
        app,
        [
            "scenario",
            "dry-run",
            str(PACKS_DIR / "investor_portal" / "scenarios"),
            "--pack",
            str(PACKS_DIR / "investor_portal"),
        ],
    )
    assert result.exit_code == 0, result.output
    margaret = result.output.split("## margaret-ato")[1].split("##")[0]
    assert "First reached High at step 2, MfaEnrolled (+4m); peak Critical." in margaret


def test_dry_run_with_older_rules_misses_the_graph(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "scenario",
            "dry-run",
            str(PACKS_DIR / "investor_portal" / "scenarios"),
            "--pack",
            str(PACKS_DIR / "investor_portal"),
            "--rules",
            "v1",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "G2" not in result.output


def test_dataset_generate_writes_the_file(tmp_path: Path) -> None:
    out = tmp_path / "ds.jsonl"
    result = runner.invoke(
        app,
        [
            "dataset",
            "generate",
            "--pack",
            str(PACKS_DIR / "digital_banking"),
            "--seed",
            "3",
            "--accounts",
            "80",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    assert "80 accounts" in result.output
