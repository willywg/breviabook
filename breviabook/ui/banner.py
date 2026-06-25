"""Startup banner — the BreviaBook wordmark in ASCII, shown once on an interactive run."""

from __future__ import annotations

from rich.align import Align
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from breviabook import __version__

# Block-style wordmark (kept as a raw string so the box-drawing glyphs are literal).
_ART = r"""
██████╗ ██████╗ ███████╗██╗   ██╗██╗ █████╗
██╔══██╗██╔══██╗██╔════╝██║   ██║██║██╔══██╗
██████╔╝██████╔╝█████╗  ██║   ██║██║███████║
██╔══██╗██╔══██╗██╔══╝  ╚██╗ ██╔╝██║██╔══██║
██████╔╝██║  ██║███████╗ ╚████╔╝ ██║██║  ██║
╚═════╝ ╚═╝  ╚═╝╚══════╝  ╚═══╝  ╚═╝╚═╝  ╚═╝
"""

_TAGLINE = "condense · translate · keep the code & figures"


def banner_renderable() -> Panel:
    """Build the banner panel (separated from printing so it is unit-testable)."""
    body = Text(_ART.strip("\n"), style="bold cyan")
    body.append("\n\n")
    body.append("BreviaBook", style="bold white")
    body.append("  ·  ", style="dim")
    body.append(_TAGLINE, style="dim")
    body.append(f"    v{__version__}", style="dim cyan")
    return Panel(Align.left(body), border_style="cyan", expand=False, padding=(0, 2))


def print_banner(console: Console) -> None:
    """Print the BreviaBook banner to ``console``."""
    console.print(banner_renderable())
