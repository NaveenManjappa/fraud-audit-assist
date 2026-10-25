from __future__ import annotations

from pathlib import Path

import pytest
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


def test_serve_reports_bad_settings_without_a_traceback(monkeypatch: pytest.MonkeyPatch) -> None:
    from fraud_audit_assist.config import get_settings

    monkeypatch.setenv("FAA_LLM__PROVIDER", "openai")
    get_settings.cache_clear()
    try:
        result = runner.invoke(app, ["serve"])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 2
    assert "llm: LLM provider 'openai' needs an API key (FAA_LLM__API_KEY)" in result.output
    assert "Traceback" not in result.output


def test_serve_reports_a_missing_rules_version(monkeypatch: pytest.MonkeyPatch) -> None:
    from fraud_audit_assist.config import get_settings

    monkeypatch.setenv("FAA_PACK_DIR", str(PACKS_DIR / "investor_portal"))
    monkeypatch.setenv("FAA_RULES_VERSION", "v9")
    get_settings.cache_clear()
    try:
        result = runner.invoke(app, ["serve"])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 2
    assert "has no rules v9; available: ['v1', 'v2', 'v3']" in result.output
