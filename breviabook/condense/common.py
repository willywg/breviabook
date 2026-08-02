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
        paras = split_paragraphs(raw)
        # 1→1 only: copy presentation/anchor shell. Divergent merge/split drops shell attrs
        # (better unwrap a lost anchor at render than invent a wrong target).
        if len(source_blocks) == 1 and len(paras) == 1:
            src = source_blocks[0]
            assert isinstance(src, ParagraphBlock)
            return [ParagraphBlock(text=paras[0], align=src.align, anchor_id=src.anchor_id)]
        return [ParagraphBlock(text=para) for para in paras]

    if isinstance(raw, list):
        # Preferred form: every entry says which source block it came from, so the
        # mapping is carried in the response instead of inferred from position.
        addressed = _addressed_entries(raw)
        if addressed is not None:
            return _parse_addressed_run(addressed, source_blocks)

        if len(raw) != len(source_blocks):
            # Positional fallback, for a model that answered without addresses.
            # Only a run carrying a list or a quote needs its blocks to line up:
            # those have a type to preserve, and losing the alignment means losing
            # which entry was the list. A run of nothing but paragraphs has no such
            # identity — merging two into one is what condensing *is*, and the
            # string form of this very response is already allowed to come back
            # with any number of paragraphs. Demanding an exact count here was
            # rejecting the model for doing what the prompt asked.
            if structured:
                raise CondenseError(
                    f"block count mismatch: expected {len(source_blocks)}, got {len(raw)}"
                )
            return [ParagraphBlock(text=_paragraph_text(entry)) for entry in raw]
        return [
            _parse_block_entry(entry, source)
            for entry, source in zip(raw, source_blocks, strict=True)
        ]

    # No key for this run at all. Distinct from an explicit empty value: the model
    # never spoke about this text, so treating it as "condensed to nothing" would
    # silently delete prose the user wrote. Let the caller keep the original.
    if raw is None:
        raise CondenseError("no entry returned for this run")

    if raw == "":
        if structured:
            raise CondenseError("structured run requires JSON array response, got empty value")
        return []

    raise CondenseError(f"unexpected texts value type: {type(raw).__name__}")


def parse_run_or_keep(raw: object | None, source_blocks: list[Block]) -> tuple[list[Block], bool]:
    """Parse one run, falling back to its source blocks. Returns ``(blocks, degraded)``.

    Validation used to be all-or-nothing per chunk/chapter: one run the model got
    structurally wrong threw away every correct run beside it, and the retry sent
    the identical prompt, so a deterministic disagreement failed all three times.
    A run that cannot be read is now the only thing that suffers — it keeps its
    original wording while the rest of the pass is condensed normally.
    """
    try:
        return parse_condensed_run(raw, source_blocks), False
    except CondenseError:
        return list(source_blocks), True


def _addressed_entries(raw: list[object]) -> list[tuple[int, dict[str, object]]] | None:
    """The ``(block number, entry)`` pairs, when *every* entry names its source.

    All-or-nothing on purpose: a half-addressed array is a model that lost track
    of the contract mid-response, and guessing which half to trust is worse than
    falling back to the positional reading. ``None`` means "not this form".
    """
    pairs: list[tuple[int, dict[str, object]]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            return None
        index = entry.get("block")
        # `isinstance(True, int)` is true, and a boolean is not a block number.
        if isinstance(index, bool):
            return None
        if isinstance(index, str) and index.strip().isdigit():
            index = int(index)
        if not isinstance(index, int):
            return None
        pairs.append((index, entry))
    return pairs or None


def _parse_addressed_run(
    pairs: list[tuple[int, dict[str, object]]], source_blocks: list[Block]
) -> list[Block]:
    """Rebuild a run from entries that name their source block.

    This is the form that makes condensing and structure compatible. Position
    could never express "these two paragraphs became one" without also losing
    which entry used to be the list; an explicit address expresses both. So a
    paragraph may be merged into a neighbour (no entry names it) or split across
    several (several entries name it), while a list or a quote must still be
    accounted for exactly once — its type is the thing there is to lose.
    """
    count = len(source_blocks)
    out: list[Block] = []
    times_named: dict[int, int] = {}
    previous = 0

    for index, entry in pairs:
        if not 1 <= index <= count:
            raise CondenseError(f"entry names block {index}, but this run has {count}")
        if index < previous:
            # Reordering prose silently rewrites the argument the author made.
            raise CondenseError(f"block {index} follows block {previous}; entries must ascend")
        previous = index

        source = source_blocks[index - 1]
        seen = times_named.get(index, 0)
        if seen and not isinstance(source, ParagraphBlock):
            raise CondenseError(f"block {index} is a {source.type} and cannot be split")
        times_named[index] = seen + 1

        block = _parse_block_entry(entry, source)
        if seen and isinstance(block, ParagraphBlock):
            # A split copies the source's shell onto the first piece only: one
            # anchor id cannot name two places, and duplicating it would break
            # every link that points at it.
            block = ParagraphBlock(text=block.text, align=block.align)
        out.append(block)

    for position, source in enumerate(source_blocks, start=1):
        if position not in times_named and isinstance(source, (ListBlock, QuoteBlock)):
            raise CondenseError(f"block {position} is a {source.type} and cannot be dropped")
    return out


def _parse_block_entry(entry: object, source: Block) -> Block:
    if isinstance(source, ParagraphBlock):
        text = _paragraph_text(entry)
        # Preserve presentation shell from source (LLM only returns content).
        return ParagraphBlock(text=text, align=source.align, anchor_id=source.anchor_id)
    if isinstance(source, ListBlock):
        return _parse_list_entry(entry, source)
    if isinstance(source, QuoteBlock):
        return _parse_quote_entry(entry, source)
    raise CondenseError(f"unexpected source block type: {type(source).__name__}")


def _paragraph_text(entry: object) -> str:
    if isinstance(entry, str):
        text = entry.strip()
    # A missing "type" is fine when the entry addresses its source block: the
    # type is the source's, and we would ignore a contradicting one anyway. A
    # *wrong* type still fails — that is the model trying to turn prose into a
    # list, which is a rewrite, not a condensation.
    elif isinstance(entry, dict) and entry.get("type") in (None, "paragraph"):
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
    return ListBlock(
        items=items,
        ordered=source.ordered,
        marker_type=source.marker_type,
        marker_color=source.marker_color,
        anchor_id=source.anchor_id,
    )


def _parse_quote_entry(entry: object, source: QuoteBlock) -> QuoteBlock:
    if not isinstance(entry, dict) or entry.get("type") != "quote":
        raise CondenseError("expected quote block")
    raw = entry.get("text")
    if not isinstance(raw, str) or not raw.strip():
        raise CondenseError("quote block missing text")
    return QuoteBlock(text=raw.strip(), align=source.align, anchor_id=source.anchor_id)


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
