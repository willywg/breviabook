"""Phase 5: per-chapter synthesis + bounded length control (mock provider)."""

from __future__ import annotations

import json

from brevia.condense.condenser import CondensedChunk
from brevia.condense.synthesizer import Synthesizer, synthesized_to_document
from brevia.ir.models import (
    CodeBlock,
    Document,
    DocumentMetadata,
    ImageAsset,
    ImageBlock,
    ParagraphBlock,
)
from brevia.llm.base import Message


class QueueProvider:
    """Returns successive replies; repeats the last once exhausted. Records call count."""

    name = "queue"

    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.calls = 0

    async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
        self.calls += 1
        return self.replies[min(self.calls - 1, len(self.replies) - 1)]


class BoomProvider:
    name = "boom"

    async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
        raise AssertionError("provider should not be called")


def _cc(blocks: list, *, idx: int = 0, title: str = "A", input_tokens: int = 100, kept=None):
    return CondensedChunk(
        id=f"ch{idx}-1",
        chapter_index=idx,
        chapter_title=title,
        blocks=blocks,
        input_tokens=input_tokens,
        kept_image_ids=kept or [],
    )


def _texts(*pairs: str) -> str:
    return json.dumps({"texts": {str(i + 1): t for i, t in enumerate(pairs)}})


async def test_smooths_multiple_chunks_preserving_code_and_images() -> None:
    chunks = [
        _cc([ParagraphBlock(text="intro a")], input_tokens=40),
        _cc(
            [CodeBlock(language="python", text="x = 1\n"), ParagraphBlock(text="outro b")],
            input_tokens=40,
        ),
    ]
    # merged blocks: [para(intro a), code, para(outro b)] -> two text runs around the code
    provider = QueueProvider([_texts("smoothed intro", "smoothed outro")])
    chapters = await Synthesizer(provider, "m").synthesize(chunks)

    assert len(chapters) == 1
    kinds = [b.type for b in chapters[0].blocks]
    assert kinds == ["paragraph", "code", "paragraph"]
    code = chapters[0].blocks[1]
    assert isinstance(code, CodeBlock) and code.text == "x = 1\n"


async def test_under_budget_no_trim_pass() -> None:
    chunk = _cc([ParagraphBlock(text="something")], input_tokens=100)
    provider = QueueProvider([_texts("tiny")])  # well under 30-token target
    chapters = await Synthesizer(provider, "m").synthesize([chunk])
    assert chapters[0].trim_passes == 0
    assert provider.calls == 1


async def test_over_budget_triggers_trim_and_reduces() -> None:
    chunk = _cc([ParagraphBlock(text="x")], input_tokens=100)  # target ~30
    long = _texts("word " * 60)  # ~60 tokens, over budget
    short = _texts("done")  # under budget
    provider = QueueProvider([long, short])
    chapters = await Synthesizer(provider, "m", tolerance=0.15).synthesize([chunk])
    assert chapters[0].trim_passes == 1
    assert provider.calls == 2
    assert chapters[0].output_tokens <= chapters[0].target_tokens * 1.15


async def test_trim_loop_is_bounded() -> None:
    chunk = _cc([ParagraphBlock(text="x")], input_tokens=100)
    long = _texts("word " * 60)  # always over budget
    provider = QueueProvider([long])  # repeats forever
    chapters = await Synthesizer(provider, "m", max_trim_passes=2).synthesize([chunk])
    assert chapters[0].trim_passes == 2  # capped
    assert provider.calls == 3  # 1 initial + 2 trim passes


async def test_code_only_chapter_passthrough_no_call() -> None:
    chunk = _cc([CodeBlock(language="python", text="x = 1\n")], input_tokens=50)
    chapters = await Synthesizer(BoomProvider(), "m").synthesize([chunk])
    assert chapters[0].trim_passes == 0
    assert len(chapters[0].blocks) == 1
    assert isinstance(chapters[0].blocks[0], CodeBlock)


async def test_target_tokens_from_original_size() -> None:
    chunks = [
        _cc([ParagraphBlock(text="a")], input_tokens=50),
        _cc([ParagraphBlock(text="b")], input_tokens=50),
    ]
    provider = QueueProvider([_texts("a", "b")])
    chapters = await Synthesizer(provider, "m", target_ratio=0.3).synthesize(chunks)
    assert chapters[0].target_tokens == round(0.3 * 100)


async def test_synthesized_to_document_keeps_only_kept_images() -> None:
    chunk = _cc(
        [ParagraphBlock(text="a"), ImageBlock(image_id="keep1")],
        kept=["keep1"],
        input_tokens=40,
    )
    provider = QueueProvider([_texts("a")])
    chapters = await Synthesizer(provider, "m").synthesize([chunk])
    original = Document(
        metadata=DocumentMetadata(title="T", source_format="epub"),
        images={
            "keep1": ImageAsset(image_id="keep1", data=b"\x89PNG", mime="image/png"),
            "drop1": ImageAsset(image_id="drop1", data=b"\x89PNG", mime="image/png"),
        },
    )
    doc = synthesized_to_document(original, chapters)
    assert set(doc.images) == {"keep1"}
    assert any(b.type == "image" for b in doc.chapters[0].blocks)


async def test_separate_chapters_stay_separate() -> None:
    chunks = [
        _cc([ParagraphBlock(text="a")], idx=0, title="One", input_tokens=40),
        _cc([ParagraphBlock(text="b")], idx=1, title="Two", input_tokens=40),
    ]
    provider = QueueProvider([_texts("a"), _texts("b")])
    chapters = await Synthesizer(provider, "m").synthesize(chunks)
    assert [c.title for c in chapters] == ["One", "Two"]
    assert [c.chapter_index for c in chapters] == [0, 1]
