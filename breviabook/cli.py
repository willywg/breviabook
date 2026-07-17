"""BreviaBook command-line interface (ROADMAP §8).

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

from breviabook import __version__
from breviabook.config import DEFAULT_CONCURRENCY, Settings, load_settings
from breviabook.llm.base import LLMProvider
from breviabook.llm.factory import get_provider
from breviabook.llm.usage import Usage
from breviabook.parsers.pdf_parser import TocEntry, load_manual_toc
from breviabook.pipeline import (
    CondenseResult,
    Estimate,
    condense_book,
    estimate_condense,
    estimate_pages,
    validate_formats,
)
from breviabook.translate.glossary import Glossary
from breviabook.ui.banner import print_banner
from breviabook.ui.progress import LogReporter, RunReporter

app = typer.Typer(
    name="breviabook",
    help="Condense large technical ebooks (EPUB/PDF), preserving code and diagrams.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


# --------------------------------------------------------------------------- #
# Shared CLI plumbing — helpers factored out so translate doesn't copy-paste
# the condense command's ~80 lines of flag-loading / validation / reporting.
# --------------------------------------------------------------------------- #


def _resolve_settings() -> Settings:
    return load_settings()


def _resolve_model(settings: Settings, model: str | None) -> str:
    return model or settings.default_model


def _load_toc(manual_toc: Path | None) -> list[TocEntry] | None:
    if manual_toc is None:
        return None
    return load_manual_toc(manual_toc)


def _load_glossary(glossary: Path | None) -> Glossary | None:
    if glossary is None:
        return None
    return Glossary.from_json(glossary)


def _build_llm(
    provider: str,
    settings: Settings,
    *,
    api_endpoint: str | None,
    reasoning_effort: str | None,
) -> LLMProvider:
    return get_provider(
        provider, settings, api_endpoint=api_endpoint, reasoning_effort=reasoning_effort
    )


def _print_dry_run_table(
    input_file: Path,
    provider: str,
    resolved_model: str,
    est: Estimate,
    translate_only: bool,
    target_ratio: float | None = None,
) -> None:
    table = Table(title="BreviaBook — dry run estimate", show_header=False)
    table.add_row("input", str(input_file))
    table.add_row("provider / model", f"{provider} / {resolved_model}")
    table.add_row("chapters", str(est.chapters))
    if translate_only:
        table.add_row("translatable units", f"{est.translatable_units:,}")
        table.add_row("batches", str(est.batches))
    else:
        table.add_row("chunks", str(est.chunks))
    table.add_row("input tokens", f"{est.input_tokens:,}")
    table.add_row("output tokens (est.)", f"{est.estimated_output_tokens:,}")
    in_pages = estimate_pages(est.input_tokens)
    out_pages = estimate_pages(est.estimated_output_tokens)
    table.add_row("approx pages", f"~{in_pages} → ~{out_pages}")
    if translate_only:
        est_ratio = est.estimated_output_tokens / est.input_tokens if est.input_tokens else 0.0
        size_pct = (est_ratio - 1.0) * 100
        table.add_row("size change (est.)", f"+{size_pct:.0f}% larger")
    else:
        est_ratio = est.estimated_output_tokens / est.input_tokens if est.input_tokens else 0.0
        table.add_row("compression (est.)", f"{(1 - est_ratio) * 100:.0f}% smaller")
        table.add_row("target ratio", f"{target_ratio:.2f}")
    table.add_row("LLM prompt tokens (est.)", f"{est.estimated_prompt_tokens:,}")
    table.add_row("LLM completion tokens (est.)", f"{est.estimated_completion_tokens:,}")
    if est.estimated_cost_usd is not None:
        table.add_row("estimated cost", f"~${est.estimated_cost_usd:.4f}")
    else:
        table.add_row("estimated cost", "n/a (model not priced / local)")
    console.print(table)
    console.print("[dim]Approximate; no LLM was called.[/]")


def _print_usage_table(usage: Usage) -> None:
    usage_table = Table(title="LLM usage", show_header=False)
    usage_table.add_row("LLM calls", f"{usage.calls}")
    usage_table.add_row("prompt tokens", f"{usage.prompt_tokens:,}")
    usage_table.add_row("completion tokens", f"{usage.completion_tokens:,}")
    usage_table.add_row("cached tokens", f"{usage.cached_tokens:,}")
    usage_table.add_row("total tokens", f"{usage.total_tokens:,}")
    cost = f"~${usage.cost_usd:.4f}" if usage.cost_usd > 0 else "n/a (model not priced)"
    usage_table.add_row("estimated cost", cost)
    console.print(usage_table)


def _print_result_table(result: CondenseResult) -> None:
    table = Table(title="Done", show_header=False)
    for path in result.output_files:
        table.add_row("output", str(path))
    table.add_row("input tokens", f"{result.input_tokens:,}")
    table.add_row("output tokens", f"{result.output_tokens:,}")
    ratio = result.output_tokens / result.input_tokens if result.input_tokens else 0.0
    in_pages = estimate_pages(result.input_tokens)
    out_pages = estimate_pages(result.output_tokens)
    table.add_row("approx pages", f"~{in_pages} → ~{out_pages}")
    if result.translate_only:
        pct = (ratio - 1.0) * 100
        sign = "+" if pct > 0 else ""
        table.add_row("size change", f"{sign}{pct:.0f}% larger (ratio {ratio:.2f})")
        if result.batches_reused:
            table.add_row("batches reused", str(result.batches_reused))
    else:
        table.add_row("compression", f"{(1 - ratio) * 100:.0f}% smaller (ratio {ratio:.2f})")
        if result.chunks_reused:
            table.add_row("chunks reused", f"{result.chunks_reused}/{result.chunks_total}")
        if result.chapters_reused:
            table.add_row("chapters reused", str(result.chapters_reused))
        if result.batches_reused:
            table.add_row("batches reused", str(result.batches_reused))
        if result.images_reused:
            table.add_row("images reused", str(result.images_reused))
    console.print(table)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


@app.command()
def version() -> None:
    """Print the installed BreviaBook version."""
    console.print(f"breviabook {__version__}")


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
    concurrency: Annotated[
        int, typer.Option(min=1, help="Maximum concurrent LLM calls per pipeline phase")
    ] = DEFAULT_CONCURRENCY,
    reasoning_effort: Annotated[
        str | None,
        typer.Option(
            help="Thinking budget for reasoning models: auto|disable|low|medium|high "
            "(Gemini defaults to 'disable' to avoid wasted cost; pass 'auto' to keep thinking)"
        ),
    ] = None,
    dry_run: Annotated[bool, typer.Option(help="Estimate tokens/cost only, no LLM call")] = False,
) -> None:
    """Condense INPUT into a shorter version (EPUB/PDF/MD), optionally translated."""
    settings = _resolve_settings()
    resolved_model = _resolve_model(settings, model)
    resolved_ratio = target_ratio if target_ratio is not None else settings.default_target_ratio

    if not input_file.exists():
        console.print(f"[red]Input not found:[/] {input_file}")
        raise typer.Exit(code=1)

    try:
        fmts = validate_formats(formats.split(","))
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    toc: list[TocEntry] | None = None
    try:
        toc = _load_toc(manual_toc)
    except (OSError, ValueError) as exc:
        console.print(f"[red]Invalid --manual-toc:[/] {exc}")
        raise typer.Exit(code=1) from exc

    glossary_obj: Glossary | None = None
    try:
        glossary_obj = _load_glossary(glossary)
    except (OSError, ValueError) as exc:
        console.print(f"[red]Invalid --glossary:[/] {exc}")
        raise typer.Exit(code=1) from exc

    if dry_run:
        try:
            est = estimate_condense(
                input_file,
                chunk_tokens=settings.default_chunk_tokens,
                target_ratio=resolved_ratio,
                manual_toc=toc,
                provider_name=provider,
                model=resolved_model,
                translate_to=translate_to,
            )
        except (NotImplementedError, ValueError) as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(code=1) from exc
        _print_dry_run_table(
            input_file,
            provider,
            resolved_model,
            est,
            translate_only=False,
            target_ratio=resolved_ratio,
        )
        return

    try:
        llm = _build_llm(
            provider, settings, api_endpoint=api_endpoint, reasoning_effort=reasoning_effort
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    if console.is_terminal:
        print_banner(console)
    console.print(
        f"[bold]Condensing[/] {input_file.name} → {', '.join(fmts)} "
        f"(provider={provider}, model={resolved_model}, ratio={resolved_ratio:.2f})"
    )

    usage_source = llm.usage
    reporter = (
        RunReporter(console, usage_source=usage_source)
        if console.is_terminal
        else LogReporter(lambda msg: console.print(f"[dim]· {msg}[/]"))
    )
    try:
        with reporter:
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
                    source_lang=source_lang,
                    glossary=glossary_obj,
                    rank_images=rank_images,
                    manual_toc=toc,
                    reporter=reporter,
                    concurrency=concurrency,
                )
            )
    except (NotImplementedError, ValueError) as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    _print_result_table(result)

    for warning in result.warnings:
        console.print(f"[yellow]⚠ {warning}[/]")

    usage = result.usage
    if usage and usage.calls:
        _print_usage_table(usage)


@app.command()
def translate(
    input_file: Annotated[Path, typer.Argument(help="Source .epub or .pdf")],
    to: Annotated[str, typer.Option("--to", help="Target language (required)")],
    source_lang: Annotated[str | None, typer.Option("--from", help="Source language")] = None,
    glossary: Annotated[Path | None, typer.Option(help="Glossary JSON for translation")] = None,
    formats: Annotated[str, typer.Option(help="Comma list of epub,pdf,md")] = "epub,pdf,md",
    out: Annotated[Path, typer.Option(help="Output directory")] = Path("./output"),
    resume: Annotated[bool, typer.Option(help="Resume from translation checkpoint")] = False,
    concurrency: Annotated[
        int, typer.Option(min=1, help="Maximum concurrent LLM calls per pipeline phase")
    ] = DEFAULT_CONCURRENCY,
    provider: Annotated[str, typer.Option(help="ollama|openai|gemini|openrouter")] = "ollama",
    model: Annotated[str | None, typer.Option(help="Model tag (defaults to config)")] = None,
    api_endpoint: Annotated[
        str | None, typer.Option(help="Base URL for OpenAI-compatible endpoints")
    ] = None,
    reasoning_effort: Annotated[
        str | None,
        typer.Option(
            help="Thinking budget for reasoning models: auto|disable|low|medium|high "
            "(Gemini defaults to 'disable' to avoid wasted cost; pass 'auto' to keep thinking)"
        ),
    ] = None,
    rank_images: Annotated[bool, typer.Option(help="Use a vision model to rank images")] = False,
    manual_toc: Annotated[
        Path | None, typer.Option(help="Manual TOC JSON for PDFs without an outline")
    ] = None,
    dry_run: Annotated[bool, typer.Option(help="Estimate tokens/cost only, no LLM call")] = False,
) -> None:
    """Translate INPUT into TARGET_LANGUAGE without condensing — full-length output."""
    settings = _resolve_settings()
    resolved_model = _resolve_model(settings, model)

    if not input_file.exists():
        console.print(f"[red]Input not found:[/] {input_file}")
        raise typer.Exit(code=1)

    try:
        fmts = validate_formats(formats.split(","))
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    toc: list[TocEntry] | None = None
    try:
        toc = _load_toc(manual_toc)
    except (OSError, ValueError) as exc:
        console.print(f"[red]Invalid --manual-toc:[/] {exc}")
        raise typer.Exit(code=1) from exc

    glossary_obj: Glossary | None = None
    try:
        glossary_obj = _load_glossary(glossary)
    except (OSError, ValueError) as exc:
        console.print(f"[red]Invalid --glossary:[/] {exc}")
        raise typer.Exit(code=1) from exc

    if dry_run:
        try:
            est = estimate_condense(
                input_file,
                manual_toc=toc,
                provider_name=provider,
                model=resolved_model,
                translate_only=True,
                translate_to=to,
            )
        except (NotImplementedError, ValueError) as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(code=1) from exc
        _print_dry_run_table(input_file, provider, resolved_model, est, translate_only=True)
        return

    try:
        llm = _build_llm(
            provider, settings, api_endpoint=api_endpoint, reasoning_effort=reasoning_effort
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    if console.is_terminal:
        print_banner(console)
    console.print(
        f"[bold]Translating[/] {input_file.name} → {to} "
        f"(provider={provider}, model={resolved_model}) → {', '.join(fmts)}"
    )

    usage_source = llm.usage
    reporter = (
        RunReporter(console, usage_source=usage_source)
        if console.is_terminal
        else LogReporter(lambda msg: console.print(f"[dim]· {msg}[/]"))
    )
    try:
        with reporter:
            result = asyncio.run(
                condense_book(
                    input_path=input_file,
                    out_dir=out,
                    formats=fmts,
                    provider=llm,
                    model=resolved_model,
                    resume=resume,
                    translate_to=to,
                    source_lang=source_lang,
                    glossary=glossary_obj,
                    rank_images=rank_images,
                    manual_toc=toc,
                    reporter=reporter,
                    translate_only=True,
                    concurrency=concurrency,
                )
            )
    except (NotImplementedError, ValueError) as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    _print_result_table(result)

    for warning in result.warnings:
        console.print(f"[yellow]⚠ {warning}[/]")

    usage = result.usage
    if usage and usage.calls:
        _print_usage_table(usage)


if __name__ == "__main__":
    app()
