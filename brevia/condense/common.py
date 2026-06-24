"""Shared primitives for the condense/synthesize passes.

Both the condenser (Phase 4) and the synthesizer (Phase 5) split blocks into prose runs vs.
structural blocks, serialize them for the LLM, and parse a JSON response. These helpers live
here so neither module duplicates them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from brevia.ir.models import (
    Block,
    CodeBlock,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    ParagraphBlock,
    QuoteBlock,
    TableBlock,
)
from brevia.utils.jsonx import extract_json_object

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
    """Render a prose run as plain text for the prompt."""
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, ListBlock):
            parts.append("\n".join(f"- {item}" for item in block.items))
        elif isinstance(block, (ParagraphBlock, QuoteBlock)):
            parts.append(block.text)
    return "\n\n".join(p for p in parts if p)


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
