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
from brevia.ir.models import Document
from brevia.llm.base import LLMProvider
from brevia.parsers.base import Parser
from brevia.parsers.epub_parser import EpubParser
from brevia.persistence.checkpoint import CheckpointManager
from brevia.render.base import Renderer
from brevia.render.epub_renderer import EpubRenderer
from brevia.render.md_renderer import MarkdownRenderer
from brevia.render.pdf_renderer import PdfRenderer
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


@dataclass
class Estimate:
    input_tokens: int
    estimated_output_tokens: int
    chapters: int
    chunks: int


def _parser_for(path: Path) -> Parser:
    suffix = path.suffix.lower()
    if suffix == ".epub":
        return EpubParser()
    if suffix == ".pdf":
        raise NotImplementedError("PDF input arrives in Phase 8; for now use an .epub.")
    raise ValueError(f"Unsupported input format: {suffix!r} (expected .epub or .pdf)")


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
    input_path: Path, *, chunk_tokens: int = 2000, target_ratio: float = 0.30
) -> Estimate:
    """Parse and report token/chunk counts without calling the LLM (``--dry-run``)."""
    doc = _parser_for(input_path).parse(input_path)
    input_tokens = count_document_tokens(doc)
    chunks = Chunker(chunk_tokens).chunk(doc)
    return Estimate(
        input_tokens=input_tokens,
        estimated_output_tokens=round(input_tokens * target_ratio),
        chapters=len(doc.chapters),
        chunks=len(chunks),
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
    log: Log = _noop,
) -> CondenseResult:
    """Run the full condensation pipeline and write the requested output formats."""
    fmts = validate_formats(formats)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{input_path.stem}-condensed"
    warnings: list[str] = []

    log(f"Parsing {input_path.name} …")
    doc = _parser_for(input_path).parse(input_path)
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
        warnings.append(f"--translate-to {translate_to}: translation arrives in Phase 10 (skipped)")

    selected = ImageSelector().select(condensed_doc)
    final_doc = selected.document

    output_files: list[Path] = []
    for fmt in fmts:
        log(f"Rendering {fmt} …")
        output_files.append(_render(fmt, final_doc, out_dir, stem))

    return CondenseResult(
        output_files=output_files,
        input_tokens=input_tokens,
        output_tokens=_document_tokens(final_doc),
        warnings=warnings,
        chunks_total=len(chunks),
        chunks_reused=reused_before if resume else 0,
    )


def _render(fmt: str, doc: Document, out_dir: Path, stem: str) -> Path:
    return _renderer_for(fmt).render(doc, out_dir, stem=stem)


def _document_tokens(doc: Document) -> int:
    return sum(block_tokens(b) for _, b in doc.iter_blocks())
