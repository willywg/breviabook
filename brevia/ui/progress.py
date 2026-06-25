"""Live progress + usage display for a condensation run.

The pipeline is UI-agnostic: it calls three small methods on a :class:`ProgressReporter` —
``note`` (a one-off line), ``phase`` (start a step, optionally with an item count), and
``advance`` (one item done). Three implementations satisfy the protocol:

- :class:`RunReporter` — a ``rich.Live`` region with a per-phase progress bar and a usage
  panel that ticks up token/cost totals live (read straight from ``provider.usage``).
- :class:`LogReporter` — plain ``· message`` lines for non-interactive output (pipes/redirects),
  so ``| grep`` and ``> file`` stay clean.
- :class:`NullReporter` — a silent no-op, the default in tests.
"""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Protocol, runtime_checkable

from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
)

from brevia.llm.usage import Usage


@runtime_checkable
class ProgressReporter(Protocol):
    """What the pipeline needs from a reporter (context management is the caller's job)."""

    def note(self, message: str) -> None: ...
    def phase(self, name: str, total: int | None = None) -> None: ...
    def advance(self, n: int = 1) -> None: ...


class NullReporter:
    """Silent reporter — the default when no UI is wanted (e.g. tests)."""

    def note(self, message: str) -> None:
        pass

    def phase(self, name: str, total: int | None = None) -> None:
        pass

    def advance(self, n: int = 1) -> None:
        pass

    def __enter__(self) -> NullReporter:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class LogReporter:
    """Plain-text reporter for non-TTY output: forwards phases/notes to a log callback."""

    def __init__(self, log: Callable[[str], None]) -> None:
        self._log = log

    def note(self, message: str) -> None:
        self._log(message)

    def phase(self, name: str, total: int | None = None) -> None:
        self._log(f"{name} …" if total is None else f"{name} (0/{total}) …")

    def advance(self, n: int = 1) -> None:
        pass

    def __enter__(self) -> LogReporter:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class RunReporter:
    """Interactive reporter: a live progress bar per phase plus a running usage panel."""

    def __init__(self, console: Console, *, usage_source: Usage | None = None) -> None:
        self.console = console
        self._usage = usage_source
        self._progress = Progress(
            SpinnerColumn(finished_text="[green]✓[/]"),
            TextColumn("[bold]{task.description}"),
            BarColumn(bar_width=22),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            console=console,
        )
        self._current: TaskID | None = None
        self._totals: dict[TaskID, int] = {}
        self._indeterminate: set[TaskID] = set()
        self._live: object | None = None  # rich.live.Live, lazily created

    # -- context management -------------------------------------------------
    def __enter__(self) -> RunReporter:
        from rich.live import Live

        live = Live(self._render(), console=self.console, refresh_per_second=12)
        live.start()
        self._live = live
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._finish_current()
        if self._live is not None:
            from rich.live import Live

            assert isinstance(self._live, Live)
            self._live.update(self._render())
            self._live.stop()
            self._live = None
        return None

    # -- ProgressReporter protocol -----------------------------------------
    def note(self, message: str) -> None:
        # Printing on the live console relocates the live region below the new line.
        self.console.print(f"[dim]· {message}[/]")

    def phase(self, name: str, total: int | None = None) -> None:
        self._finish_current()
        total_n = total if total and total > 0 else 1
        task_id = self._progress.add_task(name, total=total_n)
        self._current = task_id
        self._totals[task_id] = total_n
        if total is None or total <= 0:
            self._indeterminate.add(task_id)
        self._refresh()

    def advance(self, n: int = 1) -> None:
        if self._current is not None and self._current not in self._indeterminate:
            self._progress.advance(self._current, n)
        self._refresh()

    # -- internals ----------------------------------------------------------
    def _finish_current(self) -> None:
        if self._current is not None:
            self._progress.update(self._current, completed=self._totals[self._current])

    def _refresh(self) -> None:
        if self._live is not None:
            from rich.live import Live

            assert isinstance(self._live, Live)
            self._live.update(self._render())

    def _render(self) -> RenderableType:
        if self._usage is None:
            return self._progress
        return Group(self._progress, _usage_panel(self._usage))


def _usage_panel(usage: Usage) -> Panel:
    cost = f"~${usage.cost_usd:.4f}" if usage.cost_usd > 0 else "n/a"
    line1 = (
        f"calls [bold]{usage.calls}[/]   "
        f"in [bold]{usage.prompt_tokens:,}[/]   "
        f"out [bold]{usage.completion_tokens:,}[/]   "
        f"cached [bold]{usage.cached_tokens:,}[/]"
    )
    line2 = f"cost  [bold green]{cost}[/]"
    return Panel(
        f"{line1}\n{line2}",
        title="usage (live)",
        title_align="left",
        border_style="green",
        padding=(0, 1),
        expand=False,
    )
