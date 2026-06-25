"""TUI: banner rendering and the progress/usage reporters."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from breviabook.llm.usage import Usage
from breviabook.ui.banner import banner_renderable, print_banner
from breviabook.ui.progress import LogReporter, NullReporter, ProgressReporter, RunReporter


def _console() -> tuple[Console, StringIO]:
    buf = StringIO()
    return Console(file=buf, force_terminal=True, width=88, color_system=None), buf


def test_banner_mentions_breviabook_and_tagline() -> None:
    console, buf = _console()
    print_banner(console)
    out = buf.getvalue()
    assert "BreviaBook" in out
    assert "condense" in out and "figures" in out
    assert banner_renderable() is not None  # builds without error


def test_null_reporter_is_silent_noop() -> None:
    r = NullReporter()
    with r as ctx:
        ctx.phase("Condense", total=3)
        ctx.advance()
        ctx.note("hello")
    # Satisfies the protocol used by the pipeline.
    assert isinstance(r, ProgressReporter)


def test_log_reporter_forwards_phases_and_notes() -> None:
    lines: list[str] = []
    r = LogReporter(lines.append)
    with r:
        r.phase("Condense", total=3)
        r.advance()  # silent (no per-item spam in plain mode)
        r.note("12 chunks")
    assert "Condense (0/3) …" in lines
    assert "12 chunks" in lines
    assert isinstance(r, ProgressReporter)


def test_run_reporter_runs_a_full_cycle_and_shows_usage() -> None:
    console, buf = _console()
    usage = Usage()
    with RunReporter(console, usage_source=usage) as r:
        r.phase("Parse", total=1)
        r.advance()
        r.phase("Condense", total=2)
        usage.add(100, 50, 0, 0.0123)
        r.advance()
        usage.add(100, 50, 0, 0.0123)
        r.advance()
    out = buf.getvalue()
    assert "Condense" in out
    assert "usage (live)" in out
    assert "$" in out  # cost rendered once usage accrued


def test_run_reporter_without_usage_source_omits_panel() -> None:
    console, buf = _console()
    with RunReporter(console) as r:
        r.phase("Render", total=1)
        r.advance()
    assert "usage (live)" not in buf.getvalue()
