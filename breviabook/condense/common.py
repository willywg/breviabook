"""Shared primitives for the condense/synthesize passes.

Both the condenser (Phase 4) and the synthesizer (Phase 5) split blocks into prose runs vs.
structural blocks, serialize them for the LLM, and parse a JSON response. These helpers live
here so neither module duplicates them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from breviabook.ir.models import (
    Block,
    CodeBlock,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    ParagraphBlock,
    QuoteBlock,
    TableBlock,
)
from breviabook.utils.jsonx import extract_json_object

_PARA_SPLIT = re.compile(r"\n\s*\n")


class CondenseError(Exception):
    """Raised when an LLM response cannot be parsed into the expected result."""


@dataclass
class Segment:
    """A piece of a block sequence: a prose run, a preserved block, or an image."""

    kind: str  # "text" | "keep" | "image"
    run_id: int | None = None
    blocks: list[Block] = field(default_factory=list)  # for text runs
    block: Block | None = None  # for keep/image
    image_id: str | None = None
    caption: str | None = None


def segment_blocks(blocks: list[Block]) -> list[Segment]:
    """Split ``blocks`` into prose runs (condensable) and preserved/image segments, in order."""
    segments: list[Segment] = []
    run: list[Block] = []
    run_counter = 0

    def flush_run() -> None:
        nonlocal run, run_counter
        if run:
            run_counter += 1
            segments.append(Segment(kind="text", run_id=run_counter, blocks=run))
            run = []

    for block in blocks:
        if isinstance(block, (ParagraphBlock, QuoteBlock, ListBlock)):
            run.append(block)
        elif isinstance(block, ImageBlock):
            flush_run()
            segments.append(
                Segment(kind="image", block=block, image_id=block.image_id, caption=block.caption)
            )
        else:  # HeadingBlock, CodeBlock, TableBlock — preserved structurally
            flush_run()
            segments.append(Segment(kind="keep", block=block))
    flush_run()
    return segments


def run_text(blocks: list[Block]) -> str:
    """Render a prose run as plain text for the prompt (legacy flat form)."""
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, ListBlock):
            parts.append("\n".join(f"- {item}" for item in block.items))
        elif isinstance(block, (ParagraphBlock, QuoteBlock)):
            parts.append(block.text)
    return "\n\n".join(p for p in parts if p)


def run_has_structured_blocks(blocks: list[Block]) -> bool:
    """True when the run contains list or quote blocks (array JSON response required)."""
    return any(isinstance(block, (ListBlock, QuoteBlock)) for block in blocks)


def serialize_run(blocks: list[Block]) -> str:
    """Render a prose run with per-block type labels for the condense/synthesize contract."""
    lines: list[str] = []
    for index, block in enumerate(blocks, start=1):
        if isinstance(block, ParagraphBlock):
            lines.append(f"[BLOCK {index} type=paragraph]")
            lines.append(block.text)
        elif isinstance(block, ListBlock):
            ordered = "true" if block.ordered else "false"
            lines.append(f"[BLOCK {index} type=list ordered={ordered}]")
            if block.ordered:
                lines.extend(f"{n}. {item}" for n, item in enumerate(block.items, start=1))
            else:
                lines.extend(f"- {item}" for item in block.items)
        elif isinstance(block, QuoteBlock):
            lines.append(f"[BLOCK {index} type=quote]")
            lines.append(block.text)
    return "\n".join(lines)


def parse_condensed_run(raw: object | None, source_blocks: list[Block]) -> list[Block]:
    """Build IR blocks from one [TEXT n] response, preserving list/quote types when present."""
    structured = run_has_structured_blocks(source_blocks)

    if isinstance(raw, str):
        if structured:
            raise CondenseError("structured run requires JSON array response, got string")
        if not all(isinstance(block, ParagraphBlock) for block in source_blocks):
            raise CondenseError("structured run requires JSON array response, got string")
        return [ParagraphBlock(text=para) for para in split_paragraphs(raw)]

    if isinstance(raw, list):
        if len(raw) != len(source_blocks):
            raise CondenseError(
                f"block count mismatch: expected {len(source_blocks)}, got {len(raw)}"
            )
        return [
            _parse_block_entry(entry, source)
            for entry, source in zip(raw, source_blocks, strict=True)
        ]

    if raw is None or raw == "":
        if structured:
            raise CondenseError("structured run requires JSON array response, got empty value")
        return []

    raise CondenseError(f"unexpected texts value type: {type(raw).__name__}")


def _parse_block_entry(entry: object, source: Block) -> Block:
    if isinstance(source, ParagraphBlock):
        text = _paragraph_text(entry)
        return ParagraphBlock(text=text)
    if isinstance(source, ListBlock):
        return _parse_list_entry(entry, source)
    if isinstance(source, QuoteBlock):
        return _parse_quote_entry(entry)
    raise CondenseError(f"unexpected source block type: {type(source).__name__}")


def _paragraph_text(entry: object) -> str:
    if isinstance(entry, str):
        text = entry.strip()
    elif isinstance(entry, dict) and entry.get("type") == "paragraph":
        raw = entry.get("text")
        text = raw.strip() if isinstance(raw, str) else ""
    else:
        raise CondenseError("expected paragraph block")
    if not text:
        raise CondenseError("paragraph block missing text")
    return text


def _parse_list_entry(entry: object, source: ListBlock) -> ListBlock:
    if not isinstance(entry, dict) or entry.get("type") != "list":
        raise CondenseError("expected list block")
    items_raw = entry.get("items")
    if not isinstance(items_raw, list) or not items_raw:
        raise CondenseError("list block missing items")
    items = [str(item).strip() for item in items_raw]
    items = [item for item in items if item]
    if not items:
        raise CondenseError("list block has no non-empty items")
    return ListBlock(items=items, ordered=source.ordered)


def _parse_quote_entry(entry: object) -> QuoteBlock:
    if not isinstance(entry, dict) or entry.get("type") != "quote":
        raise CondenseError("expected quote block")
    raw = entry.get("text")
    if not isinstance(raw, str) or not raw.strip():
        raise CondenseError("quote block missing text")
    return QuoteBlock(text=raw.strip())


def structural_marker(block: Block | None) -> str:
    """Render a non-prose block as a context marker the model must not rewrite."""
    if isinstance(block, HeadingBlock):
        return f"[HEADING] {'#' * block.level} {block.text}"
    if isinstance(block, CodeBlock):
        fence = f"```{block.language or ''}\n{block.text.rstrip()}\n```"
        return f"[CODE BLOCK - preserved verbatim, do not reproduce]\n{fence}"
    if isinstance(block, TableBlock):
        return "[TABLE - preserved]"
    if isinstance(block, ImageBlock):
        cap = f' — "{block.caption}"' if block.caption else ""
        return f"[IMAGE{cap}]"
    return ""


def split_paragraphs(text: str) -> list[str]:
    """Split condensed text into non-empty paragraphs on blank lines."""
    return [p.strip() for p in _PARA_SPLIT.split(text) if p.strip()]


def extract_json(text: str) -> dict[str, object]:
    """Extract the first top-level JSON object from a model response (tolerant of fences)."""
    try:
        return extract_json_object(text)
    except ValueError as exc:
        raise CondenseError(str(exc)) from exc
