"""CLI smoke tests via Typer's CliRunner (no LLM: dry-run + error paths)."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from breviabook.cli import app
from breviabook.config import DEFAULT_CONCURRENCY

runner = CliRunner()
FIXTURE = Path(__file__).parent / "fixtures" / "sample.epub"


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "breviabook" in result.stdout


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


# --- translate command ---


def test_translate_dry_run_reports_estimate() -> None:
    result = runner.invoke(app, ["translate", str(FIXTURE), "--to", "Spanish", "--dry-run"])
    assert result.exit_code == 0
    assert "dry run estimate" in result.stdout
    assert "translatable units" in result.stdout
    assert "batches" in result.stdout
    assert "size change" in result.stdout
    assert "compression" not in result.stdout  # no compression in translate-only


def test_translate_missing_to_exits_nonzero() -> None:
    result = runner.invoke(app, ["translate", str(FIXTURE)])
    # --to is required
    assert result.exit_code != 0


def test_translate_missing_input_exits_nonzero() -> None:
    result = runner.invoke(
        app, ["translate", "does-not-exist.epub", "--to", "Spanish", "--dry-run"]
    )
    assert result.exit_code == 1


def test_translate_unknown_format_exits_nonzero() -> None:
    result = runner.invoke(
        app, ["translate", str(FIXTURE), "--to", "Spanish", "--formats", "docx", "--dry-run"]
    )
    assert result.exit_code == 1


def test_translate_unknown_provider_exits_nonzero() -> None:
    result = runner.invoke(
        app,
        ["translate", str(FIXTURE), "--to", "Spanish", "--provider", "nope", "--formats", "md"],
    )
    assert result.exit_code == 1


def test_condense_dry_run_still_shows_compression() -> None:
    # Regression: the condense command must still report compression, not size change.
    result = runner.invoke(app, ["condense", str(FIXTURE), "--dry-run"])
    assert result.exit_code == 0
    assert "compression" in result.stdout
    assert "size change" not in result.stdout


def test_commands_expose_concurrency_with_a_positive_default() -> None:
    for command in ("condense", "translate"):
        result = runner.invoke(app, [command, "--help"])
        assert result.exit_code == 0
        assert "--concurrency" in result.stdout
        assert str(DEFAULT_CONCURRENCY) in result.stdout


def test_concurrency_rejects_zero() -> None:
    result = runner.invoke(app, ["condense", str(FIXTURE), "--concurrency", "0", "--dry-run"])
    assert result.exit_code != 0
