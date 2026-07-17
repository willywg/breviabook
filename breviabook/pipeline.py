"""End-to-end condensation pipeline (ROADMAP §5).

UI-agnostic orchestration of the tested components:
``parse → chunk → condense → synthesize → select images → render``. The provider is injected
so this is unit-testable with the mock provider, and the PDF renderer is only touched when
``pdf`` is requested (it lazy-imports weasyprint).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from breviabook.condense.chunker import Chunker, count_document_tokens
from breviabook.condense.condenser import Condenser
from breviabook.condense.synthesizer import Synthesizer, synthesized_to_document
from breviabook.config import DEFAULT_CONCURRENCY
from breviabook.images.selector import ImageSelector
from breviabook.images.vision_ranker import VisionRanker
from breviabook.ir.models import Document
from breviabook.llm.base import LLMProvider, VisionProvider
from breviabook.llm.pricing import estimate_cost
from breviabook.llm.usage import Usage
from breviabook.parsers.epub_parser import EpubParser
from breviabook.parsers.pdf_parser import PdfParser, TocEntry
from breviabook.parsers.toc_inference import infer_toc
from breviabook.persistence.checkpoint import CheckpointManager
from breviabook.render.base import Renderer
from breviabook.render.epub_renderer import EpubRenderer
from breviabook.render.md_renderer import MarkdownRenderer
from breviabook.render.pdf_renderer import PdfRenderer
from breviabook.translate.glossary import Glossary
from breviabook.translate.translator import (
    DEFAULT_UNITS_PER_BATCH,
    Translator,
    count_translatable_units,
)
from breviabook.ui.progress import LogReporter, NullReporter, ProgressReporter
from breviabook.utils.tokens import block_tokens

SUPPORTED_FORMATS = ("md", "epub", "pdf")

# Rough page-count heuristic: a printed technical page holds ~1,800 chars ≈ ~450 tokens.
# Used only for human-friendly "~N pages" reporting, never for any pipeline decision.
TOKENS_PER_PAGE = 450

# Translation-only cost model constants (PRP feat/translate-command).
# TRANSLATION_EXPANSION: target-language expansion factor (EN→ES runs ~10–20% longer).
# PROMPT_OVERHEAD_PER_BATCH: per-batch prompt boilerplate (~250 tokens).
TRANSLATION_EXPANSION = 1.15
PROMPT_OVERHEAD_PER_BATCH = 250

Log = Callable[[str], None]


def estimate_pages(tokens: int) -> int:
    """Approximate printed-page count for a token total (for display only)."""
    return max(1, round(tokens / TOKENS_PER_PAGE))


def _noop(_: str) -> None:
    pass


@dataclass
class CondenseResult:
    output_files: list[Path]
    input_tokens: int
    output_tokens: int
    warnings: list[str] = field(default_factory=list)
    chunks_total: int = 0
    chunks_reused: int = 0
    usage: Usage | None = None
    translate_only: bool = False
    batches_reused: int = 0
    chapters_reused: int = 0
    images_reused: int = 0


@dataclass
class Estimate:
    input_tokens: int
    estimated_output_tokens: int
    chapters: int
    chunks: int
    estimated_prompt_tokens: int = 0
    estimated_completion_tokens: int = 0
    estimated_cost_usd: float | None = None
    translatable_units: int = 0
    batches: int = 0


def _check_supported(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix not in (".epub", ".pdf"):
        raise ValueError(f"Unsupported input format: {suffix!r} (expected .epub or .pdf)")
    return suffix


async def _parse_input(
    path: Path,
    *,
    manual_toc: list[TocEntry] | None,
    provider: LLMProvider | None,
    model: str,
    infer_pages: int,
) -> Document:
    """Parse EPUB or PDF into the IR. For PDF, resolve the TOC (outline → manual → LLM)."""
    if _check_supported(path) == ".epub":
        return EpubParser().parse(path)
    parser = PdfParser()
    extracted = parser.extract(path)
    toc = manual_toc if manual_toc is not None else (extracted.outline or None)
    if toc is None and provider is not None:
        inferred = await infer_toc(provider, model, extracted.page_texts, max_pages=infer_pages)
        toc = inferred or None
    return parser.build(extracted, toc)


def _parse_input_sync(path: Path, *, manual_toc: list[TocEntry] | None) -> Document:
    """Synchronous parse for dry-run (no LLM): EPUB, or PDF via outline/manual TOC only."""
    if _check_supported(path) == ".epub":
        return EpubParser().parse(path)
    return PdfParser().parse(path, toc=manual_toc)


def _renderer_for(fmt: str) -> Renderer:
    renderers: dict[str, Renderer] = {
        "md": MarkdownRenderer(),
        "epub": EpubRenderer(),
        "pdf": PdfRenderer(),
    }
    return renderers[fmt]


def validate_formats(formats: list[str]) -> list[str]:
    """Normalize/validate requested formats, preserving order and dropping duplicates."""
    seen: list[str] = []
    for fmt in formats:
        key = fmt.strip().lower()
        if key not in SUPPORTED_FORMATS:
            raise ValueError(f"Unknown format {fmt!r}; choose from {', '.join(SUPPORTED_FORMATS)}")
        if key not in seen:
            seen.append(key)
    if not seen:
        raise ValueError("No output formats requested")
    return seen


def estimate_condense(
    input_path: Path,
    *,
    chunk_tokens: int = 2000,
    target_ratio: float = 0.30,
    manual_toc: list[TocEntry] | None = None,
    provider_name: str | None = None,
    model: str | None = None,
    translate_to: str | None = None,
    translate_only: bool = False,
) -> Estimate:
    """Parse and report token/chunk counts + approximate cost, with NO LLM call (``--dry-run``).

    When ``translate_only``, uses the translation cost model instead of the condensation one.
    """
    doc = _parse_input_sync(input_path, manual_toc=manual_toc)
    input_tokens = count_document_tokens(doc)

    if translate_only:
        units = count_translatable_units(doc)
        batches = math.ceil(units / DEFAULT_UNITS_PER_BATCH) if units else 0
        prompt_est = round(input_tokens + PROMPT_OVERHEAD_PER_BATCH * batches)
        completion_est = round(input_tokens * TRANSLATION_EXPANSION)
        tr_cost: float | None = None
        if provider_name and model:
            tr_cost = estimate_cost(provider_name.lower(), model, prompt_est, completion_est)
        return Estimate(
            input_tokens=input_tokens,
            estimated_output_tokens=completion_est,
            chapters=len(doc.chapters),
            chunks=0,
            estimated_prompt_tokens=prompt_est,
            estimated_completion_tokens=completion_est,
            estimated_cost_usd=tr_cost,
            translatable_units=units,
            batches=batches,
        )

    chunks = Chunker(chunk_tokens).chunk(doc)
    n_chunks = len(chunks)

    # Approximate token flow across passes: condense (reads full input), synthesis (reads the
    # condensed text), and translation if requested; plus per-chunk prompt overhead.
    out = input_tokens * target_ratio
    translate = bool(translate_to)
    prompt_est = round(input_tokens + out + (out if translate else 0) + 250 * n_chunks)
    completion_est = round(out * (2 + (1 if translate else 0)))

    cost: float | None = None
    if provider_name and model:
        cost = estimate_cost(provider_name.lower(), model, prompt_est, completion_est)

    return Estimate(
        input_tokens=input_tokens,
        estimated_output_tokens=round(input_tokens * target_ratio),
        chapters=len(doc.chapters),
        chunks=n_chunks,
        estimated_prompt_tokens=prompt_est,
        estimated_completion_tokens=completion_est,
        estimated_cost_usd=cost,
    )


async def condense_book(
    *,
    input_path: Path,
    out_dir: Path,
    formats: list[str],
    provider: LLMProvider,
    model: str,
    target_ratio: float = 0.30,
    chunk_tokens: int = 2000,
    resume: bool = False,
    checkpoint_path: Path | None = None,
    translate_to: str | None = None,
    source_lang: str | None = None,
    glossary: Glossary | None = None,
    rank_images: bool = False,
    manual_toc: list[TocEntry] | None = None,
    infer_pages: int = 20,
    log: Log = _noop,
    reporter: ProgressReporter | None = None,
    translate_only: bool = False,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> CondenseResult:
    """Run the full condensation pipeline and write the requested output formats.

    When ``translate_only=True``, skips chunk→condense→synthesize and feeds the parsed
    ``Document`` directly to the :class:`Translator`, then runs ``ImageSelector`` and renders.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    if reporter is None:
        reporter = LogReporter(log) if log is not _noop else NullReporter()
    fmts = validate_formats(formats)
    out_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    batches_reused = 0
    chunks_total = 0
    chunks_reused_val = 0
    chapters_reused_val = 0
    images_reused_val = 0
    run_checkpoint: CheckpointManager | None = None
    stem_condensed = f"{input_path.stem}-condensed"

    reporter.phase("Parse", total=1)
    doc = await _parse_input(
        input_path, manual_toc=manual_toc, provider=provider, model=model, infer_pages=infer_pages
    )
    input_tokens = count_document_tokens(doc)
    reporter.advance()
    reporter.note(f"{len(doc.chapters)} chapters · ~{input_tokens:,} tokens")

    if translate_only:
        if not translate_to:
            raise ValueError("translate_only requires translate_to")
        stem = f"{input_path.stem}-{translate_to.lower().replace(' ', '-')}"
        reporter.phase("Translate", total=len(doc.chapters))

        tr_checkpoint_path = checkpoint_path or (out_dir / ".breviabook" / f"{stem}.jsonl")
        tr_checkpoint = CheckpointManager(tr_checkpoint_path)
        run_checkpoint = tr_checkpoint
        if not resume:
            tr_checkpoint.clear()
        translator = Translator(
            provider,
            model,
            translate_to,
            source_lang=source_lang,
            glossary=glossary,
            checkpoint=tr_checkpoint,
        )
        working_doc = await translator.translate_document(
            doc, concurrency=concurrency, on_progress=lambda _ch: reporter.advance()
        )
        batches_reused = translator.reused_batches
        if translator.untranslated_units:
            warnings.append(
                f"{translator.untranslated_units} segments left untranslated "
                "(model response unparseable after retries)"
            )
    else:
        stem = stem_condensed
        chunks = Chunker(chunk_tokens).chunk(doc)
        chunks_total = len(chunks)
        reporter.note(f"{chunks_total} chunks")

        checkpoint_path = checkpoint_path or (out_dir / ".breviabook" / f"{stem}.jsonl")
        checkpoint = CheckpointManager(checkpoint_path)
        run_checkpoint = checkpoint
        if not resume:
            checkpoint.clear()

        reporter.phase("Condense", total=chunks_total)
        condenser = Condenser(provider, model, target_ratio)
        condensed = await condenser.condense(
            chunks,
            concurrency=concurrency,
            checkpoint=checkpoint,
            on_progress=lambda _cc: reporter.advance(),
        )
        chunks_reused_val = condenser.reused_chunks
        warnings.extend(
            f"chunk {cc.id}: condensed output longer than input"
            for cc in condensed
            if cc.output_longer_than_input
        )
        warnings.extend(
            f"chunk {cc.id}: condense failed after retries; kept original text"
            for cc in condensed
            if cc.condense_failed
        )

        n_chapters = len({cc.chapter_index for cc in condensed})
        reporter.phase("Synthesize", total=n_chapters)
        synthesizer = Synthesizer(provider, model, target_ratio)
        chapters = await synthesizer.synthesize(
            condensed,
            concurrency=concurrency,
            checkpoint=checkpoint,
            on_progress=lambda _ch: reporter.advance(),
        )
        chapters_reused_val = synthesizer.reused_chapters
        warnings.extend(
            f"chapter {ch.chapter_index}: synthesis failed after retries; kept condensed text"
            for ch in chapters
            if ch.synthesis_failed
        )
        working_doc = synthesized_to_document(doc, chapters)

        if translate_to:
            reporter.phase("Translate", total=len(working_doc.chapters))
            translator = Translator(
                provider,
                model,
                translate_to,
                source_lang=source_lang,
                glossary=glossary,
                checkpoint=checkpoint,
            )
            working_doc = await translator.translate_document(
                working_doc, concurrency=concurrency, on_progress=lambda _ch: reporter.advance()
            )
            batches_reused = translator.reused_batches
            if translator.untranslated_units:
                warnings.append(
                    f"{translator.untranslated_units} segments left untranslated "
                    "(model response unparseable after retries)"
                )

    if rank_images and working_doc.images:
        if not isinstance(provider, VisionProvider):
            raise ValueError(
                "--rank-images needs a vision-capable provider/model (e.g. gemini); "
                f"{getattr(provider, 'name', 'provider')!r} does not support images."
            )
        vision_ranker = VisionRanker(provider, model)
        reporter.phase("Rank images", total=vision_ranker.rankable_count(working_doc))
        working_doc = await vision_ranker.rank(
            working_doc,
            concurrency=concurrency,
            checkpoint=run_checkpoint,
            on_progress=lambda _verdict: reporter.advance(),
        )
        images_reused_val = vision_ranker.reused_images

    selected = ImageSelector().select(working_doc)
    final_doc = selected.document

    reporter.phase("Render", total=len(fmts))
    output_files: list[Path] = []
    for fmt in fmts:
        try:
            output_files.append(_render(fmt, final_doc, out_dir, stem))
        except RuntimeError as exc:
            # e.g. PDF requested but weasyprint's system libs are missing: skip this format
            # and keep the others rather than discarding the whole (already paid-for) run.
            warnings.append(f"{fmt}: skipped — {exc}")
        reporter.advance()

    usage = getattr(provider, "usage", None)
    return CondenseResult(
        output_files=output_files,
        input_tokens=input_tokens,
        output_tokens=_document_tokens(final_doc),
        warnings=warnings,
        chunks_total=chunks_total,
        chunks_reused=chunks_reused_val,
        usage=usage if isinstance(usage, Usage) else None,
        translate_only=translate_only,
        batches_reused=batches_reused,
        chapters_reused=chapters_reused_val,
        images_reused=images_reused_val,
    )


def _render(fmt: str, doc: Document, out_dir: Path, stem: str) -> Path:
    return _renderer_for(fmt).render(doc, out_dir, stem=stem)


def _document_tokens(doc: Document) -> int:
    return sum(block_tokens(b) for _, b in doc.iter_blocks())
