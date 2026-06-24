"""CLI smoke tests via Typer's CliRunner (no LLM: dry-run + error paths)."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from brevia.cli import app

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "sample.epub"


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "brevia" in result.stdout


def test_dry_run_reports_estimate_without_llm() -> None:
    result = runner.invoke(app, ["condense", str(FIXTURE), "--dry-run"])
    assert result.exit_code == 0
    assert "dry run estimate" in result.stdout
    assert "input tokens" in result.stdout


def test_missing_input_exits_nonzero() -> None:
    result = runner.invoke(app, ["condense", "does-not-exist.epub", "--dry-run"])
    assert result.exit_code == 1


def test_unknown_format_exits_nonzero() -> None:
    result = runner.invoke(app, ["condense", str(FIXTURE), "--formats", "docx", "--dry-run"])
    assert result.exit_code == 1


def test_unknown_provider_exits_nonzero() -> None:
    # Not a dry run, so provider is built; unknown provider fails cleanly before any LLM call.
    result = runner.invoke(app, ["condense", str(FIXTURE), "--provider", "nope", "--formats", "md"])
    assert result.exit_code == 1
