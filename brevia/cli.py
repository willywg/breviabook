"""Brevia command-line interface (ROADMAP §8).

Phase 0 ships the CLI skeleton: ``version`` works, and ``condense`` parses every flag and
prints the resolved configuration. The actual pipeline (parse -> chunk -> condense ->
synthesize -> translate -> render) is wired in later phases.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from brevia import __version__
from brevia.config import load_settings
from brevia.llm.factory import get_provider
from brevia.parsers.pdf_parser import TocEntry, load_manual_toc
from brevia.pipeline import condense_book, estimate_condense, validate_formats

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
    manual_toc: Annotated[
        Path | None, typer.Option(help="Manual TOC JSON for PDFs without an outline")
    ] = None,
    out: Annotated[Path, typer.Option(help="Output directory")] = Path("./output"),
    resume: Annotated[bool, typer.Option(help="Resume from checkpoint")] = False,
    dry_run: Annotated[bool, typer.Option(help="Estimate tokens/cost only, no LLM call")] = False,
) -> None:
    """Condense INPUT into a shorter version (EPUB/PDF/MD), optionally translated."""
    settings = load_settings()
    resolved_model = model or settings.default_model
    resolved_ratio = target_ratio if target_ratio is not None else settings.default_target_ratio

    if not input_file.exists():
        console.print(f"[red]Input not found:[/] {input_file}")
        raise typer.Exit(code=1)

    try:
        fmts = validate_formats(formats.split(","))
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    if rank_images:
        console.print("[yellow]--rank-images[/] (vision ranking) arrives in Phase 11; ignoring.")

    toc: list[TocEntry] | None = None
    if manual_toc is not None:
        try:
            toc = load_manual_toc(manual_toc)
        except (OSError, ValueError) as exc:
            console.print(f"[red]Invalid --manual-toc:[/] {exc}")
            raise typer.Exit(code=1) from exc

    if dry_run:
        try:
            est = estimate_condense(
                input_file,
                chunk_tokens=settings.default_chunk_tokens,
                target_ratio=resolved_ratio,
                manual_toc=toc,
            )
        except (NotImplementedError, ValueError) as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(code=1) from exc
        table = Table(title="Brevia — dry run estimate", show_header=False)
        table.add_row("input", str(input_file))
        table.add_row("chapters", str(est.chapters))
        table.add_row("chunks", str(est.chunks))
        table.add_row("input tokens (est.)", f"{est.input_tokens:,}")
        table.add_row("output tokens (est.)", f"{est.estimated_output_tokens:,}")
        table.add_row("target ratio", f"{resolved_ratio:.2f}")
        console.print(table)
        console.print("[dim]No LLM was called. Cost estimation arrives in Phase 12.[/]")
        return

    try:
        llm = get_provider(provider, settings, api_endpoint=api_endpoint)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    console.print(
        f"[bold]Condensing[/] {input_file.name} → {', '.join(fmts)} "
        f"(provider={provider}, model={resolved_model}, ratio={resolved_ratio:.2f})"
    )
    try:
        result = asyncio.run(
            condense_book(
                input_path=input_file,
                out_dir=out,
                formats=fmts,
                provider=llm,
                model=resolved_model,
                target_ratio=resolved_ratio,
                chunk_tokens=settings.default_chunk_tokens,
                resume=resume,
                translate_to=translate_to,
                manual_toc=toc,
                log=lambda msg: console.print(f"[dim]· {msg}[/]"),
            )
        )
    except (NotImplementedError, ValueError) as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    table = Table(title="Done", show_header=False)
    for path in result.output_files:
        table.add_row("output", str(path))
    table.add_row("input tokens", f"{result.input_tokens:,}")
    table.add_row("output tokens", f"{result.output_tokens:,}")
    ratio = result.output_tokens / result.input_tokens if result.input_tokens else 0.0
    table.add_row("achieved ratio", f"{ratio:.2f}")
    if resume and result.chunks_reused:
        table.add_row("chunks reused", f"{result.chunks_reused}/{result.chunks_total}")
    console.print(table)
    for warning in result.warnings:
        console.print(f"[yellow]⚠ {warning}[/]")


if __name__ == "__main__":
    app()
