"""Bounded, ordered intra-phase concurrency with deterministic delayed providers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from breviabook.condense.chunker import Chunk
from breviabook.condense.condenser import CondensedChunk, Condenser
from breviabook.condense.synthesizer import Synthesizer
from breviabook.images.vision_ranker import Verdict, VisionRanker
from breviabook.ir.models import (
    Chapter,
    Document,
    DocumentMetadata,
    ImageAsset,
    ImageBlock,
    ParagraphBlock,
)
from breviabook.llm.base import Message
from breviabook.llm.usage import Usage
from breviabook.persistence.checkpoint import CheckpointManager
from breviabook.translate.translator import Translator


class DelayedProvider:
    """Completes calls in reverse order while recording the active-call peak."""

    name = "delayed"

    def __init__(self) -> None:
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.usage = Usage()

    async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
        self.calls += 1
        call = self.calls
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01 * (4 - call))
            self.usage.add(1, 2, 0, 0.01)
            content = messages[-1]["content"]
            if '"translations"' in content:
                return json.dumps({"translations": {"1": f"translated-{call}"}})
            return json.dumps({"texts": {"1": f"result-{call}"}, "essential_images": []})
        finally:
            self.active -= 1

    async def generate_with_image(
        self, prompt: str, images: list[tuple[bytes, str]], model: str, **opts: object
    ) -> str:
        self.calls += 1
        call = self.calls
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01 * (4 - call))
            self.usage.add(1, 2, 0, 0.01)
            image_id = images[0][0].decode("ascii")
            return json.dumps({"score": 1.0, "caption": f"caption-{image_id}"})
        finally:
            self.active -= 1


def _chunks() -> list[Chunk]:
    return [
        Chunk(
            id=f"ch0-{index}",
            chapter_index=0,
            chapter_title="Chapter",
            blocks=[ParagraphBlock(text=f"source-{index}")],
            token_count=10,
        )
        for index in range(1, 4)
    ]


async def test_condense_is_bounded_ordered_and_durably_checkpointed(tmp_path: Path) -> None:
    provider = DelayedProvider()
    checkpoint = CheckpointManager(tmp_path / "run.jsonl")
    progress: list[str] = []

    result = await Condenser(provider, "m").condense(
        _chunks(),
        concurrency=2,
        checkpoint=checkpoint,
        on_progress=lambda chunk: progress.append(chunk.id),
    )

    assert [chunk.id for chunk in result] == ["ch0-1", "ch0-2", "ch0-3"]
    assert provider.max_active == 2
    assert sorted(progress) == ["ch0-1", "ch0-2", "ch0-3"]
    assert set(CheckpointManager(checkpoint.path).results()) == {"ch0-1", "ch0-2", "ch0-3"}
    assert provider.usage.calls == 3
    assert provider.usage.prompt_tokens == 3
    assert provider.usage.completion_tokens == 6


def _synthesis_chunks() -> list[CondensedChunk]:
    return [
        CondensedChunk(
            id=f"ch{chapter}-1",
            chapter_index=chapter,
            chapter_title=f"Chapter {chapter}",
            blocks=[ParagraphBlock(text=f"first-{chapter}")],
            input_tokens=100,
        )
        for chapter in range(3)
    ] + [
        CondensedChunk(
            id=f"ch{chapter}-2",
            chapter_index=chapter,
            chapter_title=f"Chapter {chapter}",
            blocks=[ParagraphBlock(text=f"second-{chapter}")],
            input_tokens=100,
        )
        for chapter in range(3)
    ]


async def test_synthesis_is_bounded_and_retains_chapter_order() -> None:
    # Contiguous grouping is part of the Synthesizer contract, so order by chapter before calling.
    chunks = sorted(_synthesis_chunks(), key=lambda chunk: chunk.chapter_index)
    provider = DelayedProvider()

    result = await Synthesizer(provider, "m").synthesize(chunks, concurrency=2)

    assert [chapter.chapter_index for chapter in result] == [0, 1, 2]
    assert provider.max_active == 2


async def test_translation_is_bounded_and_retains_document_order() -> None:
    doc = Document(
        metadata=DocumentMetadata(title="T", source_format="epub"),
        chapters=[
            Chapter(blocks=[ParagraphBlock(text="first")]),
            Chapter(blocks=[ParagraphBlock(text="second")]),
            Chapter(blocks=[ParagraphBlock(text="third")]),
        ],
    )
    provider = DelayedProvider()
    progress: list[str] = []

    translated = await Translator(provider, "m", "Spanish").translate_document(
        doc,
        concurrency=2,
        on_progress=lambda chapter: progress.append(chapter.blocks[0].text),  # type: ignore[union-attr]
    )

    assert [chapter.blocks[0].text for chapter in translated.chapters] == [  # type: ignore[union-attr]
        "translated-1",
        "translated-2",
        "translated-3",
    ]
    assert provider.max_active == 2
    assert len(progress) == 3


async def test_vision_is_bounded_ordered_and_reports_each_image() -> None:
    doc = Document(
        metadata=DocumentMetadata(title="T", source_format="epub"),
        images={
            image_id: ImageAsset(image_id=image_id, data=image_id.encode("ascii"), mime="image/png")
            for image_id in ("one", "two", "three")
        },
        chapters=[
            Chapter(
                blocks=[
                    ParagraphBlock(text="context"),
                    ImageBlock(image_id="one"),
                    ImageBlock(image_id="two"),
                    ImageBlock(image_id="three"),
                ]
            )
        ],
    )
    provider = DelayedProvider()
    progress: list[Verdict] = []
    ranker = VisionRanker(provider, "m")

    ranked = await ranker.rank(doc, concurrency=2, on_progress=progress.append)

    captions = [block.caption for _, block in ranked.iter_blocks() if isinstance(block, ImageBlock)]
    assert ranker.rankable_count(doc) == 3
    assert captions == ["caption-one", "caption-two", "caption-three"]
    assert provider.max_active == 2
    assert len(progress) == 3
