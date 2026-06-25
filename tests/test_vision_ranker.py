"""Phase 11: vision image ranking (Strategy B) with a fake vision provider."""

from __future__ import annotations

import json
from typing import Any

from breviabook.images.vision_ranker import VisionRanker, build_vision_prompt
from breviabook.ir.models import (
    Chapter,
    Document,
    DocumentMetadata,
    ImageAsset,
    ImageBlock,
    ParagraphBlock,
)
from breviabook.llm.base import Message


class FakeVisionProvider:
    """Scores images by id via a provided mapping; records calls."""

    name = "fakevision"

    def __init__(self, scores: dict[str, float], caption: str | None = None) -> None:
        self.scores = scores
        self.caption = caption
        self.image_calls = 0
        self.last_prompt = ""

    async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
        return "{}"

    async def generate_with_image(
        self, prompt: str, images: list[tuple[bytes, str]], model: str, **opts: object
    ) -> str:
        self.image_calls += 1
        self.last_prompt = prompt
        # Identify the image by its bytes tail (tests encode the id there).
        data = images[0][0]
        image_id = data.decode("ascii").split("|")[-1]
        out: dict[str, Any] = {"score": self.scores.get(image_id, 0.0), "essential": True}
        if self.caption:
            out["caption"] = self.caption
        return json.dumps(out)


def _doc_with_images(*ids: str) -> Document:
    images = {
        iid: ImageAsset(image_id=iid, data=f"x|{iid}".encode(), mime="image/png") for iid in ids
    }
    blocks: list[Any] = [ParagraphBlock(text="surrounding context paragraph")]
    for iid in ids:
        blocks.append(ImageBlock(image_id=iid, caption=f"cap-{iid}"))
    return Document(
        metadata=DocumentMetadata(title="T", source_format="epub"),
        images=images,
        chapters=[Chapter(title="C", blocks=blocks)],
    )


def test_build_vision_prompt_contains_context() -> None:
    prompt = build_vision_prompt("some context")
    assert "some context" in prompt
    assert "score" in prompt


async def test_keeps_high_score_drops_low_score() -> None:
    doc = _doc_with_images("keep", "drop")
    provider = FakeVisionProvider({"keep": 0.9, "drop": 0.1})
    out = await VisionRanker(provider, "m", threshold=0.5).rank(doc)
    assert set(out.images) == {"keep"}  # low-scored asset pruned
    image_ids = [b.image_id for _, b in out.iter_blocks() if isinstance(b, ImageBlock)]
    assert image_ids == ["keep"]
    assert provider.image_calls == 2


async def test_updates_caption_when_enabled() -> None:
    doc = _doc_with_images("img")
    provider = FakeVisionProvider({"img": 0.8}, caption="A clearer caption")
    out = await VisionRanker(provider, "m", update_captions=True).rank(doc)
    block = next(b for _, b in out.iter_blocks() if isinstance(b, ImageBlock))
    assert block.caption == "A clearer caption"


async def test_keeps_original_caption_when_disabled() -> None:
    doc = _doc_with_images("img")
    provider = FakeVisionProvider({"img": 0.8}, caption="ignored")
    out = await VisionRanker(provider, "m", update_captions=False).rank(doc)
    block = next(b for _, b in out.iter_blocks() if isinstance(b, ImageBlock))
    assert block.caption == "cap-img"


async def test_parse_failure_keeps_image() -> None:
    class BrokenProvider(FakeVisionProvider):
        async def generate_with_image(self, prompt, images, model, **opts):  # type: ignore[no-untyped-def]
            return "not json"

    doc = _doc_with_images("img")
    out = await VisionRanker(BrokenProvider({}), "m").rank(doc)
    assert set(out.images) == {"img"}  # safe default: keep on parse failure
