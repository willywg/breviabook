"""Strategy B image ranking with a vision model (ROADMAP §7.1, opt-in ``--rank-images``).

For each image kept after condensation, send the image plus its surrounding text to a
vision-capable model, score how essential it is (meaningful diagram/figure/chart vs.
decorative), and drop those below a threshold — optionally regenerating a concise caption.
Runs before the structural Strategy A selector, which then prunes the dropped assets.

Checkpoint keys are ``img:{image_id}``: image ids are assumed unique per document (the IR
builds them per book). If two different images ever shared an id, the fingerprint — which
includes the image bytes — degrades the collision to a cache-miss, never to a wrong reuse.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable

from pydantic import BaseModel, Field, ValidationError

from breviabook.config import DEFAULT_CONCURRENCY
from breviabook.ir.models import (
    Block,
    Chapter,
    Document,
    HeadingBlock,
    ImageAsset,
    ImageBlock,
    ParagraphBlock,
    QuoteBlock,
)
from breviabook.llm.base import VisionProvider
from breviabook.persistence.checkpoint import CheckpointManager
from breviabook.persistence.fingerprint import Fingerprint
from breviabook.utils.jsonx import extract_json_object

_CONTEXT_CHARS = 600


class Verdict(BaseModel):
    """The model's keep/drop decision for one image (cached per image id)."""

    keep: bool
    score: float
    caption: str | None = None
    # Runtime-only: False when the model's reply could not be parsed. A parse failure
    # keeps the image (safe default) but is NOT cacheable — a resume retries it.
    parsed: bool = Field(default=True, exclude=True)


def build_vision_prompt(context: str) -> str:
    return (
        "You are deciding whether to keep an image in a condensed version of a technical book. "
        "Judge whether it is a meaningful diagram, figure, chart, screenshot, or equation that "
        "aids understanding — as opposed to decorative, redundant, or low-information.\n\n"
        f"Context around the image:\n{context or '(none)'}\n\n"
        'Return ONLY JSON: {"score": 0.0-1.0, "essential": true/false, '
        '"caption": "concise caption"}'
    )


class VisionRanker:
    """Scores images with a vision model and drops the unimportant ones (Strategy B)."""

    def __init__(
        self,
        provider: VisionProvider,
        model: str,
        *,
        threshold: float = 0.5,
        update_captions: bool = True,
    ) -> None:
        self.provider = provider
        self.model = model
        self.threshold = threshold
        self.update_captions = update_captions
        self.reused_images = 0

    @staticmethod
    def rankable_count(doc: Document) -> int:
        """Return the number of image blocks with an asset eligible for vision ranking."""
        return sum(
            1
            for chapter in doc.chapters
            for block in chapter.blocks
            if isinstance(block, ImageBlock) and block.image_id in doc.images
        )

    async def rank(
        self,
        doc: Document,
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        checkpoint: CheckpointManager | None = None,
        on_progress: Callable[[Verdict], None] | None = None,
    ) -> Document:
        """Rank images concurrently while rebuilding the document in its original order."""
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        work: list[tuple[int, int, ImageBlock, ImageAsset, str, str]] = []
        for chapter_index, chapter in enumerate(doc.chapters):
            for block_index, block in enumerate(chapter.blocks):
                if isinstance(block, ImageBlock) and block.image_id in doc.images:
                    asset = doc.images[block.image_id]
                    context = _surrounding_text(chapter.blocks, block_index, block.caption)
                    source_hash = _image_fingerprint(
                        context, asset, self.model, self.threshold, self.update_captions
                    )
                    work.append((chapter_index, block_index, block, asset, context, source_hash))

        semaphore = asyncio.Semaphore(concurrency)

        async def rank_one(
            item: tuple[int, int, ImageBlock, ImageAsset, str, str],
        ) -> Verdict:
            _, _, block, asset, context, source_hash = item
            verdict = self._cached_verdict(checkpoint, block.image_id, source_hash)
            if verdict is not None:
                self.reused_images += 1
            else:
                async with semaphore:
                    verdict = await self._rank(asset, context)
                if checkpoint is not None and verdict.parsed:
                    checkpoint.record(
                        f"img:{block.image_id}",
                        {
                            "source_hash": source_hash,
                            "verdict": verdict.model_dump(mode="json"),
                        },
                    )
            if on_progress is not None:
                on_progress(verdict)
            return verdict

        # gather keeps the work-plan order even when calls complete out of order.
        verdicts = await asyncio.gather(*(rank_one(item) for item in work))
        verdict_at = {
            (chapter_index, block_index): verdict
            for (chapter_index, block_index, *_), verdict in zip(work, verdicts, strict=True)
        }
        chapters: list[Chapter] = []
        for chapter_index, chapter in enumerate(doc.chapters):
            new_blocks: list[Block] = []
            for block_index, block in enumerate(chapter.blocks):
                verdict = verdict_at.get((chapter_index, block_index))
                if verdict is not None and isinstance(block, ImageBlock):
                    if verdict.keep:
                        caption = block.caption
                        if self.update_captions and verdict.caption:
                            caption = verdict.caption
                        new_blocks.append(block.model_copy(update={"caption": caption}))
                    # dropped: omit the ImageBlock
                else:
                    new_blocks.append(block)
            chapters.append(Chapter(title=chapter.title, blocks=new_blocks))

        referenced = {b.image_id for ch in chapters for b in ch.blocks if isinstance(b, ImageBlock)}
        images = {iid: asset for iid, asset in doc.images.items() if iid in referenced}
        return Document(metadata=doc.metadata, images=images, chapters=chapters)

    @staticmethod
    def _cached_verdict(
        checkpoint: CheckpointManager | None, image_id: str, source_hash: str
    ) -> Verdict | None:
        """Return the cached verdict, or ``None`` to re-rank (same rules as condense)."""
        if checkpoint is None:
            return None
        payload = checkpoint.get(f"img:{image_id}")
        if payload is None or payload.get("source_hash") != source_hash:
            return None
        try:
            return Verdict.model_validate(payload.get("verdict"))
        except ValidationError:
            return None

    async def _rank(self, asset: ImageAsset, context: str) -> Verdict:
        raw = await self.provider.generate_with_image(
            build_vision_prompt(context), [(asset.data, asset.mime)], self.model
        )
        try:
            obj = extract_json_object(raw)
        except ValueError:
            # Parse failure → keep (safe), flagged unparsed so it is never cached.
            return Verdict(keep=True, score=1.0, parsed=False)
        score = _as_float(obj.get("score"))
        caption = obj.get("caption")
        return Verdict(
            keep=score >= self.threshold,
            score=score,
            caption=caption if isinstance(caption, str) and caption.strip() else None,
        )


def _image_fingerprint(
    context: str,
    asset: ImageAsset,
    model: str,
    threshold: float,
    update_captions: bool,
) -> str:
    """SHA-1 over everything that determines one image's verdict.

    Covers the model and ranking parameters, the exact context string sent to the model,
    and the image content itself — same image id with new bytes must re-rank.
    """
    fp = Fingerprint()
    fp.field(model)
    fp.field(repr(threshold))
    fp.field(repr(update_captions))
    fp.field(context)
    fp.field(asset.mime)
    fp.field(hashlib.sha256(asset.data).hexdigest())
    return fp.hexdigest()


def _surrounding_text(blocks: list[Block], index: int, caption: str | None) -> str:
    parts: list[str] = []
    if caption:
        parts.append(f"Current caption: {caption}")
    before = _nearest_text(blocks, index, step=-1)
    after = _nearest_text(blocks, index, step=1)
    if before:
        parts.append(before)
    if after:
        parts.append(after)
    return "\n".join(parts)[:_CONTEXT_CHARS]


def _nearest_text(blocks: list[Block], index: int, *, step: int) -> str | None:
    i = index + step
    while 0 <= i < len(blocks):
        block = blocks[i]
        if isinstance(block, (HeadingBlock, ParagraphBlock, QuoteBlock)):
            return block.text
        i += step
    return None


def _as_float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
