"""Per-chapter synthesis — level 2 of the hierarchical summarization (ROADMAP §7.3).

Per-chunk condensation (Phase 4) is choppy and only *asks* the model for a ratio. This pass
takes a chapter's condensed chunks, smooths transitions across chunk boundaries, and actively
trims toward ``target_ratio`` using a bounded length-control loop. Code, tables, and the
images already kept in Phase 4 are preserved verbatim — only prose runs go to the LLM.
"""

from __future__ import annotations

from collections.abc import Callable
from itertools import groupby

from pydantic import BaseModel, Field

from breviabook.condense.common import (
    CondenseError,
    Segment,
    extract_json,
    run_text,
    segment_blocks,
    split_paragraphs,
    structural_marker,
)
from breviabook.condense.condenser import CondensedChunk
from breviabook.condense.prompts import build_synthesize_messages
from breviabook.ir.models import Block, Chapter, Document, ParagraphBlock
from breviabook.llm.base import LLMProvider
from breviabook.utils.tokens import block_tokens


class SynthesizedChapter(BaseModel):
    """A smoothed, length-controlled chapter."""

    chapter_index: int
    title: str | None = None
    blocks: list[Block] = Field(default_factory=list)
    kept_image_ids: list[str] = Field(default_factory=list)
    input_tokens: int = 0  # original chapter size (drives the budget)
    target_tokens: int = 0
    output_tokens: int = 0
    trim_passes: int = 0


class Synthesizer:
    """Smooths and length-controls condensed chapters via an :class:`LLMProvider`."""

    def __init__(
        self,
        provider: LLMProvider,
        model: str,
        target_ratio: float = 0.30,
        *,
        tolerance: float = 0.15,
        max_trim_passes: int = 2,
        min_target_tokens: int = 100,
        max_retries: int = 3,
    ) -> None:
        self.provider = provider
        self.model = model
        self.target_ratio = target_ratio
        self.tolerance = tolerance
        self.max_trim_passes = max_trim_passes
        # Retry a malformed-JSON pass a few times; if it keeps failing, keep the current text
        # instead of crashing the run.
        self.max_retries = max_retries
        # Don't chase a target below this — tiny chapters can't be meaningfully compressed,
        # and doing so triggers pointless trim passes (wasted LLM calls).
        self.min_target_tokens = min_target_tokens

    async def synthesize(
        self,
        condensed: list[CondensedChunk],
        *,
        on_progress: Callable[[SynthesizedChapter], None] | None = None,
    ) -> list[SynthesizedChapter]:
        """Synthesize each chapter from its condensed chunks (input order preserved)."""
        results: list[SynthesizedChapter] = []
        for _, group in groupby(condensed, key=lambda cc: cc.chapter_index):
            chapter_chunks = list(group)
            results.append(await self.synthesize_chapter(chapter_chunks))
            if on_progress is not None:
                on_progress(results[-1])
        return results

    async def synthesize_chapter(self, chunks: list[CondensedChunk]) -> SynthesizedChapter:
        first = chunks[0]
        blocks: list[Block] = [b for cc in chunks for b in cc.blocks]
        kept_image_ids = [iid for cc in chunks for iid in cc.kept_image_ids]
        input_tokens = sum(cc.input_tokens for cc in chunks)
        target_tokens = max(round(self.target_ratio * input_tokens), self.min_target_tokens)

        segments = segment_blocks(blocks)
        if not any(s.kind == "text" for s in segments):
            return self._result(first, blocks, kept_image_ids, input_tokens, target_tokens, 0)

        # A single chunk already within budget has nothing to smooth across boundaries and
        # nothing to trim — skip the LLM entirely (avoids wasted calls on small chapters).
        current_tokens = sum(block_tokens(b) for b in blocks)
        if len(chunks) == 1 and current_tokens <= target_tokens:
            return self._result(first, blocks, kept_image_ids, input_tokens, target_tokens, 0)

        smoothed = await self._synth_pass(segments, target_tokens, smooth=True)
        if smoothed is None:
            # Smoothing kept returning malformed JSON: keep the concatenated condensed text.
            return self._result(first, blocks, kept_image_ids, input_tokens, target_tokens, 0)
        blocks = smoothed
        output_tokens = sum(block_tokens(b) for b in blocks)

        passes = 0
        limit = target_tokens * (1 + self.tolerance)
        while output_tokens > limit and passes < self.max_trim_passes:
            segments = segment_blocks(blocks)
            if not any(s.kind == "text" for s in segments):
                break
            trimmed = await self._synth_pass(segments, target_tokens, smooth=False)
            if trimmed is None:
                break  # parse failed on this trim pass; keep what we have
            passes += 1
            blocks = trimmed
            output_tokens = sum(block_tokens(b) for b in blocks)

        return self._result(first, blocks, kept_image_ids, input_tokens, target_tokens, passes)

    async def _synth_pass(
        self, segments: list[Segment], target_tokens: int, *, smooth: bool
    ) -> list[Block] | None:
        """Run one synthesis pass; return ``None`` if the response can't be parsed after retries."""
        body = _serialize(segments)
        messages = build_synthesize_messages(body, target_tokens, smooth=smooth)
        for _attempt in range(self.max_retries):
            raw = await self.provider.generate(messages, self.model)
            try:
                texts = _parse_texts(raw)
            except CondenseError:
                continue
            return _reassemble(segments, texts)
        return None

    def _result(
        self,
        first: CondensedChunk,
        blocks: list[Block],
        kept_image_ids: list[str],
        input_tokens: int,
        target_tokens: int,
        passes: int,
    ) -> SynthesizedChapter:
        return SynthesizedChapter(
            chapter_index=first.chapter_index,
            title=first.chapter_title,
            blocks=blocks,
            kept_image_ids=kept_image_ids,
            input_tokens=input_tokens,
            target_tokens=target_tokens,
            output_tokens=sum(block_tokens(b) for b in blocks),
            trim_passes=passes,
        )


def _serialize(segments: list[Segment]) -> str:
    lines: list[str] = []
    for seg in segments:
        if seg.kind == "text":
            lines.append(f"[TEXT {seg.run_id}]")
            lines.append(run_text(seg.blocks))
        else:
            lines.append(structural_marker(seg.block))
        lines.append("")
    return "\n".join(lines).strip()


def _parse_texts(raw: str) -> dict[str, str]:
    obj = extract_json(raw)
    texts_raw = obj.get("texts")
    texts: dict[str, str] = {}
    if isinstance(texts_raw, dict):
        for key, value in texts_raw.items():
            if isinstance(value, str):
                texts[str(key)] = value
    return texts


def _reassemble(segments: list[Segment], texts: dict[str, str]) -> list[Block]:
    out: list[Block] = []
    for seg in segments:
        if seg.kind == "text":
            for para in split_paragraphs(texts.get(str(seg.run_id), "")):
                out.append(ParagraphBlock(text=para))
        elif seg.block is not None:  # keep or image — preserved in place
            out.append(seg.block)
    return out


def synthesized_to_document(original: Document, chapters: list[SynthesizedChapter]) -> Document:
    """Build a condensed ``Document`` from synthesized chapters (kept images only)."""
    kept_ids = {iid for ch in chapters for iid in ch.kept_image_ids}
    images = {iid: asset for iid, asset in original.images.items() if iid in kept_ids}
    doc_chapters = [Chapter(title=ch.title, blocks=ch.blocks) for ch in chapters]
    return Document(metadata=original.metadata, images=images, chapters=doc_chapters)
