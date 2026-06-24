"""Brevia command-line interface (ROADMAP §8).

Phase 0 ships the CLI skeleton: ``version`` works, and ``condense`` parses every flag and
prints the resolved configuration. The actual pipeline (parse -> chunk -> condense ->
synthesize -> translate -> render) is wired in later phases.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from brevia import __version__
from brevia.config import load_settings

app = typer.Typer(
    name="brevia",
    help="Condense large technical ebooks (EPUB/PDF), preserving code and diagrams.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@app.command()
def version() -> None:
    """Print the installed Brevia version."""
    console.print(f"brevia {__version__}")


@app.command()
def condense(
    input_file: Annotated[Path, typer.Argument(help="Source .epub or .pdf")],
    provider: Annotated[str, typer.Option(help="ollama|openai|gemini|openrouter")] = "ollama",
    model: Annotated[str | None, typer.Option(help="Model tag (defaults to config)")] = None,
    api_endpoint: Annotated[
        str | None, typer.Option(help="Base URL for OpenAI-compatible endpoints")
    ] = None,
    target_ratio: Annotated[
        float | None, typer.Option(help="Target size, e.g. 0.30 = ~30% of original")
    ] = None,
    formats: Annotated[str, typer.Option(help="Comma list of epub,pdf,md")] = "epub,pdf,md",
    source_lang: Annotated[str | None, typer.Option(help="Source language")] = None,
    translate_to: Annotated[str | None, typer.Option(help="Target language (omit = none)")] = None,
    rank_images: Annotated[bool, typer.Option(help="Use a vision model to rank images")] = False,
    glossary: Annotated[Path | None, typer.Option(help="Glossary JSON for translation")] = None,
    out: Annotated[Path, typer.Option(help="Output directory")] = Path("./output"),
    resume: Annotated[bool, typer.Option(help="Resume from checkpoint")] = False,
    dry_run: Annotated[bool, typer.Option(help="Estimate tokens/cost only, no LLM call")] = False,
) -> None:
    """Condense INPUT into a shorter version (Phase 0: parses + prints config only)."""
    settings = load_settings()
    resolved_model = model or settings.default_model
    resolved_ratio = target_ratio if target_ratio is not None else settings.default_target_ratio

    table = Table(title="Brevia — resolved run configuration", show_header=False)
    table.add_row("input", str(input_file))
    table.add_row("provider", provider)
    table.add_row("model", resolved_model)
    table.add_row("api_endpoint", api_endpoint or "(default)")
    table.add_row("target_ratio", f"{resolved_ratio:.2f}")
    table.add_row("formats", formats)
    table.add_row("source_lang", source_lang or "(auto)")
    table.add_row("translate_to", translate_to or "(none)")
    table.add_row("rank_images", str(rank_images))
    table.add_row("glossary", str(glossary) if glossary else "(none)")
    table.add_row("out", str(out))
    table.add_row("resume", str(resume))
    table.add_row("dry_run", str(dry_run))
    console.print(table)

    if dry_run:
        console.print("[yellow]--dry-run:[/] token/cost estimation arrives in Phase 12.")
        return
    console.print("[yellow]Pipeline not implemented yet[/] — building it phase by phase.")


if __name__ == "__main__":
    app()
