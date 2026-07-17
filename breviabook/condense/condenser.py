"""Per-chunk condensation — level 1 of the hierarchical summarization (ROADMAP §7.3).

Design that keeps code safe and structure intact:
- The chunk is split into *segments*: prose runs (paragraph/quote/list), kept blocks
  (heading/code/table), and image markers. Only prose runs are sent to the LLM.
- Code/tables/headings are restored from the ORIGINAL blocks, never from the model's echo —
  so code is preserved verbatim by construction (ROADMAP §7.2).
- Images are kept/dropped by id based on the model's ``essential_images`` (§7.1).
- If the condensed output is longer than the input, we flag it (§7.3, cognitivetech).

Checkpoint records are fingerprinted (see :func:`_chunk_fingerprint`) so a ``--resume``
after changing the model, the target ratio, the chunk size, or the book itself recomputes
stale chunks instead of silently reusing them. Note: ``Chunk.prev_context`` does not reach
the prompt today; if it ever does, it must join the fingerprint.

Shared segmentation/parsing primitives live in :mod:`breviabook.condense.common`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

from pydantic import BaseModel, Field, ValidationError

from breviabook.condense.chunker import Chunk
from breviabook.condense.common import (
    CondenseError,
    Segment,
    extract_json,
    parse_condensed_run,
    segment_blocks,
    serialize_run,
    structural_marker,
)
from breviabook.condense.prompts import build_condense_messages
from breviabook.config import DEFAULT_CONCURRENCY
from breviabook.ir.models import Block, Chapter, Document
from breviabook.llm.base import LLMProvider
from breviabook.persistence.checkpoint import CheckpointManager
from breviabook.persistence.fingerprint import Fingerprint
from breviabook.utils.tokens import block_tokens

__all__ = [
    "CondenseError",
    "CondensedChunk",
    "Condenser",
    "assemble_condensed_document",
]


class CondensedChunk(BaseModel):
    """The condensed form of one :class:`~breviabook.condense.chunker.Chunk`."""

    id: str
    chapter_index: int
    chapter_title: str | None = None
    blocks: list[Block] = Field(default_factory=list)
    kept_image_ids: list[str] = Field(default_factory=list)
    dropped_image_ids: list[str] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    output_longer_than_input: bool = False
    condense_failed: bool = False  # parse kept failing → kept the chunk uncondensed


class Condenser:
    """Condenses chunks via an :class:`~breviabook.llm.base.LLMProvider`."""

    def __init__(
        self,
        provider: LLMProvider,
        model: str,
        target_ratio: float = 0.30,
        *,
        max_retries: int = 3,
    ) -> None:
        self.provider = provider
        self.model = model
        self.target_ratio = target_ratio
        # Models occasionally emit malformed JSON (more so with thinking disabled). Retry a
        # few times, then keep the chunk uncondensed rather than crash the whole book.
        self.max_retries = max_retries
        self.reused_chunks = 0

    async def condense(
        self,
        chunks: list[Chunk],
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        checkpoint: CheckpointManager | None = None,
        on_progress: Callable[[CondensedChunk], None] | None = None,
    ) -> list[CondensedChunk]:
        """Condense every chunk, reusing checkpoint records whose fingerprint still matches."""
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        semaphore = asyncio.Semaphore(concurrency)

        async def condense_one(chunk: Chunk) -> CondensedChunk:
            source_hash = _chunk_fingerprint(chunk, self.model, self.target_ratio)
            cc = self._cached_chunk(checkpoint, chunk.id, source_hash)
            if cc is not None:
                self.reused_chunks += 1
            else:
                async with semaphore:
                    cc = await self.condense_chunk(chunk)
                # Only successful chunks are cacheable: a failed (uncondensed) chunk must
                # stay retryable — a resume is precisely the chance to retry it.
                if checkpoint is not None and not cc.condense_failed:
                    checkpoint.record(
                        chunk.id,
                        {"source_hash": source_hash, "chunk": cc.model_dump(mode="json")},
                    )
            if on_progress is not None:
                on_progress(cc)
            return cc

        # gather preserves chunk order even when provider calls finish out of order.
        return list(await asyncio.gather(*(condense_one(chunk) for chunk in chunks)))

    @staticmethod
    def _cached_chunk(
        checkpoint: CheckpointManager | None, chunk_id: str, source_hash: str
    ) -> CondensedChunk | None:
        """Return the cached chunk, or ``None`` to recompute it.

        A record is reused only when its ``source_hash`` matches *and* the inner payload
        validates as a :class:`CondensedChunk` — a torn or hand-edited record is treated
        as stale. Old bare-format records (the chunk payload itself, no ``source_hash``)
        fail the hash check and are recomputed once.
        """
        if checkpoint is None:
            return None
        payload = checkpoint.get(chunk_id)
        if payload is None or payload.get("source_hash") != source_hash:
            return None
        try:
            return CondensedChunk.model_validate(payload.get("chunk"))
        except ValidationError:
            return None

    async def condense_chunk(self, chunk: Chunk) -> CondensedChunk:
        segments = segment_blocks(chunk.blocks)
        if not any(s.kind == "text" for s in segments):
            return self._passthrough(chunk, segments)

        body, image_ids = _serialize(segments)
        messages = build_condense_messages(body, self.target_ratio, image_ids)
        for _attempt in range(self.max_retries):
            raw = await self.provider.generate(messages, self.model)
            try:
                texts, essential = _parse_response(raw)
                return self._reassemble(chunk, segments, texts, essential)
            except CondenseError:
                continue  # retry with a fresh generation
        # All retries failed to parse: keep the chunk uncondensed and flag it.
        cc = self._passthrough(chunk, segments)
        cc.condense_failed = True
        return cc

    def _reassemble(
        self,
        chunk: Chunk,
        segments: list[Segment],
        texts: dict[str, object],
        essential: set[str],
    ) -> CondensedChunk:
        new_blocks: list[Block] = []
        kept: list[str] = []
        dropped: list[str] = []
        for seg in segments:
            if seg.kind == "text":
                new_blocks.extend(parse_condensed_run(texts.get(str(seg.run_id)), seg.blocks))
            elif seg.kind == "keep" and seg.block is not None:
                new_blocks.append(seg.block)
            elif seg.kind == "image" and seg.image_id is not None:
                if seg.image_id in essential and seg.block is not None:
                    new_blocks.append(seg.block)
                    kept.append(seg.image_id)
                else:
                    dropped.append(seg.image_id)
        return self._build(chunk, new_blocks, kept, dropped)

    def _passthrough(self, chunk: Chunk, segments: list[Segment]) -> CondensedChunk:
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


def _chunk_fingerprint(chunk: Chunk, model: str, target_ratio: float) -> str:
    """SHA-1 over the model, the target ratio, and the chunk's ordered block content.

    Any change that alters the condense output — a different model or ratio, a different
    chunking (``--chunk-tokens``), or a different book under the same input stem (chunk ids
    are positional) — changes the fingerprint, so a stale checkpoint record is recomputed
    instead of silently reused. Blocks are hashed **in order**: the sequence is semantic,
    unlike the translator's key-sorted unit batches.
    """
    fp = Fingerprint()
    fp.field("condense_block_format:2")
    fp.field(model)
    fp.field(repr(target_ratio))
    blocks_dump = [b.model_dump(mode="json") for b in chunk.blocks]
    fp.field(json.dumps(blocks_dump, sort_keys=True, ensure_ascii=False))
    return fp.hexdigest()


def _serialize(segments: list[Segment]) -> tuple[str, list[str]]:
    """Serialize segments to the condense prompt body; return (body, image_ids)."""
    lines: list[str] = []
    image_ids: list[str] = []
    for seg in segments:
        if seg.kind == "text":
            lines.append(f"[TEXT {seg.run_id}]")
            lines.append(serialize_run(seg.blocks))
        elif seg.kind == "image" and seg.image_id is not None:
            cap = f' — "{seg.caption}"' if seg.caption else ""
            lines.append(f"[IMG:{seg.image_id}{cap}]")
            image_ids.append(seg.image_id)
        elif seg.kind == "keep":
            lines.append(structural_marker(seg.block))
        lines.append("")
    return "\n".join(lines).strip(), image_ids


def _parse_response(raw: str) -> tuple[dict[str, object], set[str]]:
    obj = extract_json(raw)
    texts_raw = obj.get("texts")
    texts: dict[str, object] = {}
    if isinstance(texts_raw, dict):
        for key, value in texts_raw.items():
            if isinstance(value, (str, list)):
                texts[str(key)] = value
    essential_raw = obj.get("essential_images")
    essential = {str(x) for x in essential_raw} if isinstance(essential_raw, list) else set()
    return texts, essential


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
