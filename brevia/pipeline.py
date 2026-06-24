"""End-to-end condensation pipeline (ROADMAP §5).

UI-agnostic orchestration of the tested components:
``parse → chunk → condense → synthesize → select images → render``. The provider is injected
so this is unit-testable with the mock provider, and the PDF renderer is only touched when
``pdf`` is requested (it lazy-imports weasyprint).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from brevia.condense.chunker import Chunker, count_document_tokens
from brevia.condense.condenser import Condenser
from brevia.condense.synthesizer import Synthesizer, synthesized_to_document
from brevia.images.selector import ImageSelector
from brevia.images.vision_ranker import VisionRanker
from brevia.ir.models import Document
from brevia.llm.base import LLMProvider, VisionProvider
from brevia.llm.pricing import estimate_cost
from brevia.llm.usage import Usage
from brevia.parsers.epub_parser import EpubParser
from brevia.parsers.pdf_parser import PdfParser, TocEntry
from brevia.parsers.toc_inference import infer_toc
from brevia.persistence.checkpoint import CheckpointManager
from brevia.render.base import Renderer
from brevia.render.epub_renderer import EpubRenderer
from brevia.render.md_renderer import MarkdownRenderer
from brevia.render.pdf_renderer import PdfRenderer
from brevia.translate.glossary import Glossary
from brevia.translate.translator import Translator
from brevia.utils.tokens import block_tokens

SUPPORTED_FORMATS = ("md", "epub", "pdf")

Log = Callable[[str], None]


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


@dataclass
class Estimate:
    input_tokens: int
    estimated_output_tokens: int
    chapters: int
    chunks: int
    estimated_prompt_tokens: int = 0
    estimated_completion_tokens: int = 0
    estimated_cost_usd: float | None = None


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
) -> Estimate:
    """Parse and report token/chunk counts + approximate cost, with NO LLM call (``--dry-run``)."""
    doc = _parse_input_sync(input_path, manual_toc=manual_toc)
    input_tokens = count_document_tokens(doc)
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
) -> CondenseResult:
    """Run the full condensation pipeline and write the requested output formats."""
    fmts = validate_formats(formats)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{input_path.stem}-condensed"
    warnings: list[str] = []

    log(f"Parsing {input_path.name} …")
    doc = await _parse_input(
        input_path, manual_toc=manual_toc, provider=provider, model=model, infer_pages=infer_pages
    )
    input_tokens = count_document_tokens(doc)

    chunks = Chunker(chunk_tokens).chunk(doc)
    log(f"Document: {len(doc.chapters)} chapters, {len(chunks)} chunks, ~{input_tokens} tokens")

    checkpoint_path = checkpoint_path or (out_dir / ".brevia" / f"{stem}.jsonl")
    checkpoint = CheckpointManager(checkpoint_path)
    if not resume:
        checkpoint.clear()
    reused_before = len(checkpoint.results())

    log("Condensing chunks …")
    condenser = Condenser(provider, model, target_ratio)
    condensed = await condenser.condense(chunks, checkpoint=checkpoint)
    warnings.extend(
        f"chunk {cc.id}: condensed output longer than input"
        for cc in condensed
        if cc.output_longer_than_input
    )

    log("Synthesizing chapters …")
    chapters = await Synthesizer(provider, model, target_ratio).synthesize(condensed)
    condensed_doc = synthesized_to_document(doc, chapters)

    if translate_to:
        log(f"Translating to {translate_to} …")
        translator = Translator(
            provider, model, translate_to, source_lang=source_lang, glossary=glossary
        )
        condensed_doc = await translator.translate_document(condensed_doc)

    if rank_images and condensed_doc.images:
        if not isinstance(provider, VisionProvider):
            raise ValueError(
                "--rank-images needs a vision-capable provider/model (e.g. gemini); "
                f"{getattr(provider, 'name', 'provider')!r} does not support images."
            )
        log("Ranking images (vision) …")
        condensed_doc = await VisionRanker(provider, model).rank(condensed_doc)

    selected = ImageSelector().select(condensed_doc)
    final_doc = selected.document

    output_files: list[Path] = []
    for fmt in fmts:
        log(f"Rendering {fmt} …")
        output_files.append(_render(fmt, final_doc, out_dir, stem))

    usage = getattr(provider, "usage", None)
    return CondenseResult(
        output_files=output_files,
        input_tokens=input_tokens,
        output_tokens=_document_tokens(final_doc),
        warnings=warnings,
        chunks_total=len(chunks),
        chunks_reused=reused_before if resume else 0,
        usage=usage if isinstance(usage, Usage) else None,
    )


def _render(fmt: str, doc: Document, out_dir: Path, stem: str) -> Path:
    return _renderer_for(fmt).render(doc, out_dir, stem=stem)


def _document_tokens(doc: Document) -> int:
    return sum(block_tokens(b) for _, b in doc.iter_blocks())
