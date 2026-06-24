"""Per-chunk condensation — level 1 of the hierarchical summarization (ROADMAP §7.3).

Design that keeps code safe and structure intact:
- The chunk is split into *segments*: prose runs (paragraph/quote/list), kept blocks
  (heading/code/table), and image markers. Only prose runs are sent to the LLM.
- Code/tables/headings are restored from the ORIGINAL blocks, never from the model's echo —
  so code is preserved verbatim by construction (ROADMAP §7.2).
- Images are kept/dropped by id based on the model's ``essential_images`` (§7.1).
- If the condensed output is longer than the input, we flag it (§7.3, cognitivetech).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from brevia.condense.chunker import Chunk
from brevia.condense.prompts import build_condense_messages
from brevia.ir.models import (
    Block,
    Chapter,
    CodeBlock,
    Document,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    ParagraphBlock,
    QuoteBlock,
    TableBlock,
)
from brevia.llm.base import LLMProvider
from brevia.persistence.checkpoint import CheckpointManager
from brevia.utils.tokens import block_tokens

_PARA_SPLIT = re.compile(r"\n\s*\n")


class CondenseError(Exception):
    """Raised when the model response cannot be parsed into a condensation result."""


class CondensedChunk(BaseModel):
    """The condensed form of one :class:`~brevia.condense.chunker.Chunk`."""

    id: str
    chapter_index: int
    chapter_title: str | None = None
    blocks: list[Block] = Field(default_factory=list)
    kept_image_ids: list[str] = Field(default_factory=list)
    dropped_image_ids: list[str] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    output_longer_than_input: bool = False


@dataclass
class _Segment:
    kind: str  # "text" | "keep" | "image"
    run_id: int | None = None
    blocks: list[Block] = field(default_factory=list)  # for text runs
    block: Block | None = None  # for keep/image
    image_id: str | None = None
    caption: str | None = None


class Condenser:
    """Condenses chunks via an :class:`~brevia.llm.base.LLMProvider`."""

    def __init__(self, provider: LLMProvider, model: str, target_ratio: float = 0.30) -> None:
        self.provider = provider
        self.model = model
        self.target_ratio = target_ratio

    async def condense(
        self,
        chunks: list[Chunk],
        *,
        checkpoint: CheckpointManager | None = None,
        on_progress: Callable[[CondensedChunk], None] | None = None,
    ) -> list[CondensedChunk]:
        """Condense every chunk, skipping any already recorded in ``checkpoint``."""
        results: list[CondensedChunk] = []
        for chunk in chunks:
            if checkpoint is not None and checkpoint.is_done(chunk.id):
                stored = checkpoint.get(chunk.id)
                cc = CondensedChunk.model_validate(stored)
            else:
                cc = await self.condense_chunk(chunk)
                if checkpoint is not None:
                    checkpoint.record(chunk.id, cc.model_dump(mode="json"))
            results.append(cc)
            if on_progress is not None:
                on_progress(cc)
        return results

    async def condense_chunk(self, chunk: Chunk) -> CondensedChunk:
        segments = _segment_chunk(chunk.blocks)
        text_segments = [s for s in segments if s.kind == "text"]
        if not text_segments:
            return self._passthrough(chunk, segments)

        body, image_ids = _serialize(segments)
        messages = build_condense_messages(body, self.target_ratio, image_ids)
        raw = await self.provider.generate(messages, self.model)
        texts, essential = _parse_response(raw)
        return self._reassemble(chunk, segments, texts, essential)

    def _reassemble(
        self,
        chunk: Chunk,
        segments: list[_Segment],
        texts: dict[str, str],
        essential: set[str],
    ) -> CondensedChunk:
        new_blocks: list[Block] = []
        kept: list[str] = []
        dropped: list[str] = []
        for seg in segments:
            if seg.kind == "text":
                condensed = texts.get(str(seg.run_id), "").strip()
                for para in _PARA_SPLIT.split(condensed):
                    cleaned = para.strip()
                    if cleaned:
                        new_blocks.append(ParagraphBlock(text=cleaned))
            elif seg.kind == "keep" and seg.block is not None:
                new_blocks.append(seg.block)
            elif seg.kind == "image" and seg.image_id is not None:
                if seg.image_id in essential and seg.block is not None:
                    new_blocks.append(seg.block)
                    kept.append(seg.image_id)
                else:
                    dropped.append(seg.image_id)
        return self._build(chunk, new_blocks, kept, dropped)

    def _passthrough(self, chunk: Chunk, segments: list[_Segment]) -> CondensedChunk:
        """No prose to condense: keep blocks and all images unchanged."""
        kept = [s.image_id for s in segments if s.kind == "image" and s.image_id]
        return self._build(chunk, list(chunk.blocks), kept, [])

    def _build(
        self, chunk: Chunk, blocks: list[Block], kept: list[str], dropped: list[str]
    ) -> CondensedChunk:
        output_tokens = sum(block_tokens(b) for b in blocks)
        return CondensedChunk(
            id=chunk.id,
            chapter_index=chunk.chapter_index,
            chapter_title=chunk.chapter_title,
            blocks=blocks,
            kept_image_ids=kept,
            dropped_image_ids=dropped,
            input_tokens=chunk.token_count,
            output_tokens=output_tokens,
            output_longer_than_input=output_tokens > chunk.token_count,
        )


# --------------------------------------------------------------------------- #
# Segmentation / serialization / parsing
# --------------------------------------------------------------------------- #


def _segment_chunk(blocks: list[Block]) -> list[_Segment]:
    segments: list[_Segment] = []
    run: list[Block] = []
    run_counter = 0

    def flush_run() -> None:
        nonlocal run, run_counter
        if run:
            run_counter += 1
            segments.append(_Segment(kind="text", run_id=run_counter, blocks=run))
            run = []

    for block in blocks:
        if isinstance(block, (ParagraphBlock, QuoteBlock, ListBlock)):
            run.append(block)
        elif isinstance(block, ImageBlock):
            flush_run()
            segments.append(
                _Segment(kind="image", block=block, image_id=block.image_id, caption=block.caption)
            )
        else:  # HeadingBlock, CodeBlock, TableBlock — preserved structurally
            flush_run()
            segments.append(_Segment(kind="keep", block=block))
    flush_run()
    return segments


def _serialize(segments: list[_Segment]) -> tuple[str, list[str]]:
    lines: list[str] = []
    image_ids: list[str] = []
    for seg in segments:
        if seg.kind == "text":
            lines.append(f"[TEXT {seg.run_id}]")
            lines.append(_run_text(seg.blocks))
        elif seg.kind == "image" and seg.image_id is not None:
            cap = f' — "{seg.caption}"' if seg.caption else ""
            lines.append(f"[IMG:{seg.image_id}{cap}]")
            image_ids.append(seg.image_id)
        elif seg.kind == "keep":
            lines.append(_keep_text(seg.block))
        lines.append("")
    return "\n".join(lines).strip(), image_ids


def _run_text(blocks: list[Block]) -> str:
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, ListBlock):
            parts.append("\n".join(f"- {item}" for item in block.items))
        elif isinstance(block, (ParagraphBlock, QuoteBlock)):
            parts.append(block.text)
    return "\n\n".join(p for p in parts if p)


def _keep_text(block: Block | None) -> str:
    if isinstance(block, HeadingBlock):
        return f"[HEADING] {'#' * block.level} {block.text}"
    if isinstance(block, CodeBlock):
        fence = f"```{block.language or ''}\n{block.text.rstrip()}\n```"
        return f"[CODE BLOCK - preserved verbatim, do not reproduce]\n{fence}"
    if isinstance(block, TableBlock):
        return "[TABLE - preserved]"
    return ""


def _parse_response(raw: str) -> tuple[dict[str, str], set[str]]:
    obj = _extract_json(raw)
    texts_raw = obj.get("texts")
    texts: dict[str, str] = {}
    if isinstance(texts_raw, dict):
        for key, value in texts_raw.items():
            if isinstance(value, str):
                texts[str(key)] = value
    essential_raw = obj.get("essential_images")
    essential = {str(x) for x in essential_raw} if isinstance(essential_raw, list) else set()
    return texts, essential


def _extract_json(text: str) -> dict[str, object]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise CondenseError("No JSON object found in model response")
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise CondenseError(f"Invalid JSON in model response: {exc}") from exc
    if not isinstance(obj, dict):
        raise CondenseError("Model response JSON is not an object")
    return obj


# --------------------------------------------------------------------------- #
# Assembly into a condensed Document
# --------------------------------------------------------------------------- #


def assemble_condensed_document(original: Document, condensed: list[CondensedChunk]) -> Document:
    """Group condensed chunks back into a condensed ``Document`` (kept images only)."""
    chapters: list[Chapter] = []
    kept_ids: set[str] = set()
    current_index: int | None = None
    for cc in condensed:
        kept_ids.update(cc.kept_image_ids)
        if cc.chapter_index != current_index:
            chapters.append(Chapter(title=cc.chapter_title, blocks=[]))
            current_index = cc.chapter_index
        chapters[-1].blocks.extend(cc.blocks)
    images = {iid: asset for iid, asset in original.images.items() if iid in kept_ids}
    return Document(metadata=original.metadata, images=images, chapters=chapters)
