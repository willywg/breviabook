"""Strategy B image ranking with a vision model (ROADMAP §7.1, opt-in ``--rank-images``).

For each image kept after condensation, send the image plus its surrounding text to a
vision-capable model, score how essential it is (meaningful diagram/figure/chart vs.
decorative), and drop those below a threshold — optionally regenerating a concise caption.
Runs before the structural Strategy A selector, which then prunes the dropped assets.
"""

from __future__ import annotations

from dataclasses import dataclass

from brevia.ir.models import (
    Block,
    Chapter,
    Document,
    HeadingBlock,
    ImageBlock,
    ParagraphBlock,
    QuoteBlock,
)
from brevia.llm.base import VisionProvider
from brevia.utils.jsonx import extract_json_object

_CONTEXT_CHARS = 600


@dataclass
class _Verdict:
    keep: bool
    score: float
    caption: str | None


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

    async def rank(self, doc: Document) -> Document:
        chapters: list[Chapter] = []
        for chapter in doc.chapters:
            new_blocks: list[Block] = []
            for index, block in enumerate(chapter.blocks):
                if isinstance(block, ImageBlock) and block.image_id in doc.images:
                    verdict = await self._rank(doc, block, chapter.blocks, index)
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

    async def _rank(
        self, doc: Document, block: ImageBlock, blocks: list[Block], index: int
    ) -> _Verdict:
        context = _surrounding_text(blocks, index, block.caption)
        asset = doc.images[block.image_id]
        raw = await self.provider.generate_with_image(
            build_vision_prompt(context), [(asset.data, asset.mime)], self.model
        )
        try:
            obj = extract_json_object(raw)
        except ValueError:
            return _Verdict(keep=True, score=1.0, caption=None)  # parse failure → keep (safe)
        score = _as_float(obj.get("score"))
        caption = obj.get("caption")
        return _Verdict(
            keep=score >= self.threshold,
            score=score,
            caption=caption if isinstance(caption, str) and caption.strip() else None,
        )


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
