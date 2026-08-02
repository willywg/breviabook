"""Phase 5 + Phase 12: per-chapter synthesis, bounded length control, small-chapter guard."""

from __future__ import annotations

import json
from pathlib import Path

from breviabook.condense.condenser import CondensedChunk
from breviabook.condense.synthesizer import Synthesizer, synthesized_to_document
from breviabook.ir.models import (
    CodeBlock,
    Document,
    DocumentMetadata,
    ImageAsset,
    ImageBlock,
    ListBlock,
    ParagraphBlock,
    QuoteBlock,
)
from breviabook.llm.base import Message
from breviabook.llm.usage import Usage
from breviabook.persistence.checkpoint import CheckpointManager


class QueueProvider:
    """Returns successive replies; repeats the last once exhausted. Records call count."""

    name = "queue"

    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.calls = 0
        self.usage = Usage()

    async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
        self.calls += 1
        return self.replies[min(self.calls - 1, len(self.replies) - 1)]


class BoomProvider:
    name = "boom"
    usage = Usage()

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


async def test_smoothing_parse_failure_keeps_condensed_text() -> None:
    # Two chunks force a smoothing call; if it keeps returning malformed JSON, fall back to
    # the concatenated condensed blocks instead of crashing.
    chunks = [
        _cc([ParagraphBlock(text="intro a")], input_tokens=40),
        _cc([ParagraphBlock(text="outro b")], input_tokens=40),
    ]
    provider = QueueProvider(["not json"])
    chapters = await Synthesizer(provider, "m", max_retries=3).synthesize(chunks)
    assert provider.calls == 3  # retried before giving up
    assert chapters[0].trim_passes == 0
    texts = [b.text for b in chapters[0].blocks]  # type: ignore[union-attr]
    assert texts == ["intro a", "outro b"]  # original condensed text preserved


async def test_single_small_chapter_skips_synthesis() -> None:
    # One chunk already within the (floored) budget → no LLM call at all (Phase 12 guard).
    chunk = _cc([ParagraphBlock(text="a short paragraph")], input_tokens=50)
    chapters = await Synthesizer(BoomProvider(), "m").synthesize([chunk])
    assert chapters[0].trim_passes == 0
    assert chapters[0].blocks[0].text == "a short paragraph"  # type: ignore[union-attr]


async def test_smooths_multiple_chunks_preserving_code() -> None:
    chunks = [
        _cc([ParagraphBlock(text="intro a")], input_tokens=40),
        _cc(
            [CodeBlock(language="python", text="x = 1\n"), ParagraphBlock(text="outro b")],
            input_tokens=40,
        ),
    ]
    provider = QueueProvider([_texts("smoothed intro", "smoothed outro")])
    chapters = await Synthesizer(provider, "m").synthesize(chunks)

    assert len(chapters) == 1
    assert provider.calls == 1  # multi-chunk smooths even when under budget
    assert chapters[0].trim_passes == 0
    kinds = [b.type for b in chapters[0].blocks]
    assert kinds == ["paragraph", "code", "paragraph"]
    code = chapters[0].blocks[1]
    assert isinstance(code, CodeBlock) and code.text == "x = 1\n"


async def test_over_budget_triggers_trim_and_reduces() -> None:
    chunks = [
        _cc([ParagraphBlock(text="x" * 50)], idx=0, input_tokens=200),
        _cc([ParagraphBlock(text="y" * 50)], idx=0, input_tokens=200),
    ]  # total 400 -> target 120
    long = _texts("word " * 200, "word " * 200)  # over budget
    short = _texts("done", "done")  # under budget
    provider = QueueProvider([long, short])
    chapters = await Synthesizer(provider, "m", tolerance=0.15).synthesize(chunks)
    assert chapters[0].trim_passes == 1
    assert provider.calls == 2
    assert chapters[0].output_tokens <= chapters[0].target_tokens * 1.15


def _over_budget_chapter() -> list[CondensedChunk]:
    """Two chunks totalling 400 input tokens, so the target is 120 and the cap 138."""
    return [
        _cc([ParagraphBlock(text="x" * 50)], idx=0, input_tokens=200),
        _cc([ParagraphBlock(text="y" * 50)], idx=0, input_tokens=200),
    ]


async def test_trim_loop_is_bounded() -> None:
    """Shrinking every pass, the cap is what stops it."""
    replies = [_texts("word " * n) for n in (200, 180, 160)]  # each shorter, none in budget
    provider = QueueProvider(replies)
    chapters = await Synthesizer(provider, "m", max_trim_passes=2).synthesize(
        _over_budget_chapter()
    )
    assert chapters[0].trim_passes == 2  # capped
    assert provider.calls == 3  # 1 smoothing + 2 trim passes


async def test_trim_stops_when_a_pass_buys_nothing() -> None:
    """A rewrite that came back no shorter is the model declining to cut further.

    Measured on a real chapter: a trim pass returned the identical token count and
    the loop paid for a second one on the same premise. The next request would be
    the same prompt at the same model, so there is nothing left to buy.
    """
    provider = QueueProvider([_texts("word " * 200)])  # same reply forever
    chapters = await Synthesizer(provider, "m", max_trim_passes=2).synthesize(
        _over_budget_chapter()
    )
    assert chapters[0].trim_passes == 1  # tried once, not twice
    assert provider.calls == 2  # 1 smoothing + the single trim


async def test_a_trim_pass_that_grew_the_text_is_discarded() -> None:
    """Paying to move backwards is worse than paying for nothing."""
    provider = QueueProvider([_texts("word " * 160), _texts("word " * 400)])
    chapters = await Synthesizer(provider, "m", max_trim_passes=2).synthesize(
        _over_budget_chapter()
    )
    assert provider.calls == 2
    assert chapters[0].output_tokens < 400  # the longer rewrite was not kept


async def test_default_is_a_single_trim_pass() -> None:
    """Two passes bought ~12% for twice the price of the pipeline's dearest call."""
    replies = [_texts("word " * n) for n in (200, 180, 160)]
    provider = QueueProvider(replies)
    chapters = await Synthesizer(provider, "m").synthesize(_over_budget_chapter())
    assert chapters[0].trim_passes == 1
    assert provider.calls == 2


async def test_code_only_chapter_passthrough_no_call() -> None:
    chunk = _cc([CodeBlock(language="python", text="x = 1\n")], input_tokens=50)
    chapters = await Synthesizer(BoomProvider(), "m").synthesize([chunk])
    assert chapters[0].trim_passes == 0
    assert len(chapters[0].blocks) == 1
    assert isinstance(chapters[0].blocks[0], CodeBlock)


async def test_target_tokens_floored_for_tiny_input() -> None:
    chunk = _cc([ParagraphBlock(text="a")], input_tokens=50)  # 0.3*50=15 -> floored to 100
    chapters = await Synthesizer(BoomProvider(), "m", min_target_tokens=100).synthesize([chunk])
    assert chapters[0].target_tokens == 100


async def test_target_tokens_from_original_size_when_above_floor() -> None:
    chunks = [
        _cc([ParagraphBlock(text="x" * 50)], idx=0, input_tokens=1000),
        _cc([ParagraphBlock(text="y" * 50)], idx=0, input_tokens=1000),
    ]
    provider = QueueProvider([_texts("a", "b")])
    chapters = await Synthesizer(provider, "m", target_ratio=0.3).synthesize(chunks)
    assert chapters[0].target_tokens == round(0.3 * 2000)  # 600, above the 100 floor


async def test_synthesized_to_document_keeps_only_kept_images() -> None:
    chunk = _cc(
        [ParagraphBlock(text="a"), ImageBlock(image_id="keep1")],
        kept=["keep1"],
        input_tokens=40,
    )
    chapters = await Synthesizer(BoomProvider(), "m").synthesize([chunk])  # single small -> skip
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
    chapters = await Synthesizer(BoomProvider(), "m").synthesize(chunks)  # both small -> skip
    assert [c.title for c in chapters] == ["One", "Two"]
    assert [c.chapter_index for c in chapters] == [0, 1]


# --- Checkpoint / fingerprint matrix (feat--checkpoint-remaining-phases) --------------- #


def _two_chunk_chapter() -> list[CondensedChunk]:
    # Two chunks force a smoothing call (a single small chunk skips synthesis entirely).
    return [
        _cc([ParagraphBlock(text="intro a")], input_tokens=40),
        _cc([ParagraphBlock(text="outro b")], input_tokens=40),
    ]


async def test_resume_reuses_synthesized_chapter(tmp_path: Path) -> None:
    cp_path = tmp_path / "run.jsonl"
    reply = _texts("smoothed intro", "smoothed outro")

    first = QueueProvider([reply])
    out1 = await Synthesizer(first, "m").synthesize(
        _two_chunk_chapter(), checkpoint=CheckpointManager(cp_path)
    )
    assert first.calls == 1

    # Resume with a provider that raises if touched: the chapter must come from the checkpoint.
    resumed = Synthesizer(BoomProvider(), "m")
    out2 = await resumed.synthesize(_two_chunk_chapter(), checkpoint=CheckpointManager(cp_path))
    assert resumed.reused_chapters == 1
    assert [b.text for b in out2[0].blocks] == [b.text for b in out1[0].blocks]  # type: ignore[union-attr]


async def test_resume_recomputes_on_model_change(tmp_path: Path) -> None:
    cp_path = tmp_path / "run.jsonl"
    reply = _texts("smoothed intro", "smoothed outro")
    await Synthesizer(QueueProvider([reply]), "model-a").synthesize(
        _two_chunk_chapter(), checkpoint=CheckpointManager(cp_path)
    )

    other = QueueProvider([reply])
    ranker = Synthesizer(other, "model-b")
    await ranker.synthesize(_two_chunk_chapter(), checkpoint=CheckpointManager(cp_path))
    assert other.calls == 1  # different model → fingerprint miss → recomputed
    assert ranker.reused_chapters == 0


async def test_resume_recomputes_on_ratio_change(tmp_path: Path) -> None:
    cp_path = tmp_path / "run.jsonl"
    reply = _texts("smoothed intro", "smoothed outro")
    await Synthesizer(QueueProvider([reply]), "m", target_ratio=0.30).synthesize(
        _two_chunk_chapter(), checkpoint=CheckpointManager(cp_path)
    )

    other = QueueProvider([reply])
    s = Synthesizer(other, "m", target_ratio=0.50)
    await s.synthesize(_two_chunk_chapter(), checkpoint=CheckpointManager(cp_path))
    assert other.calls == 1
    assert s.reused_chapters == 0


def _two_run_chapter() -> list[CondensedChunk]:
    """A chapter whose prose is split into two runs by a code block between them."""
    return [
        _cc([ParagraphBlock(text="intro a"), ParagraphBlock(text="intro b")], input_tokens=200),
        _cc(
            [
                CodeBlock(text="print(1)", language="python"),
                ParagraphBlock(text="after the code"),
                ListBlock(items=["first", "second"]),
            ],
            input_tokens=200,
        ),
    ]


async def test_one_unreadable_run_does_not_cost_the_whole_chapter() -> None:
    """The bug this guards: a single mis-shaped run used to fail every run beside it.

    Run 2 carries a list, so it keeps the strict alignment rule and the model's
    single merged entry is rejected. Run 1 must still come back smoothed, and the
    list must survive with its own items rather than being dropped.
    """
    reply = json.dumps(
        {
            "texts": {
                "1": "smoothed intro",
                "2": [{"type": "paragraph", "text": "merged the list away"}],
            }
        }
    )
    provider = QueueProvider([reply])
    out = await Synthesizer(provider, "m", target_ratio=0.50).synthesize(_two_run_chapter())

    chapter = out[0]
    assert chapter.synthesis_failed is False
    assert chapter.degraded_runs == 1
    assert provider.calls == 1  # a readable pass is not re-bought

    texts = [b.text for b in chapter.blocks if isinstance(b, ParagraphBlock)]
    assert "smoothed intro" in texts  # run 1 was smoothed
    assert "after the code" in texts  # run 2 kept its source wording
    lists = [b for b in chapter.blocks if isinstance(b, ListBlock)]
    assert [b.items for b in lists] == [["first", "second"]]


def _mixed_run_chapter() -> list[CondensedChunk]:
    """A chapter whose second run holds two paragraphs *and* a list.

    The exact shape that produced the residual overshoot: the model merges the
    two paragraphs, as the prompt asks, and under a positional contract that
    shifted the list out of alignment and cost the whole run its trim.
    """
    return [
        _cc([ParagraphBlock(text="intro a"), ParagraphBlock(text="intro b")], input_tokens=200),
        _cc(
            [
                CodeBlock(text="print(1)", language="python"),
                ParagraphBlock(text="first point at length"),
                ParagraphBlock(text="second point at length"),
                ListBlock(items=["first", "second"]),
            ],
            input_tokens=200,
        ),
    ]


async def test_addressed_reply_merges_paragraphs_without_degrading() -> None:
    """The fix, end to end: a merge that used to degrade now condenses cleanly.

    Run 2's two paragraphs collapse into one entry and the list still lands as a
    list — so the run is trimmed rather than kept at its source length, which is
    what the overshoot was made of.
    """
    reply = json.dumps(
        {
            "texts": {
                "1": [{"block": 1, "type": "paragraph", "text": "smoothed intro"}],
                "2": [
                    {"block": 1, "type": "paragraph", "text": "both points, briefly"},
                    {"block": 3, "type": "list", "items": ["first", "second"]},
                ],
            }
        }
    )
    provider = QueueProvider([reply])
    out = await Synthesizer(provider, "m", target_ratio=0.50).synthesize(_mixed_run_chapter())

    chapter = out[0]
    assert chapter.synthesis_failed is False
    assert chapter.degraded_runs == 0  # the whole point

    texts = [b.text for b in chapter.blocks if isinstance(b, ParagraphBlock)]
    assert texts == ["smoothed intro", "both points, briefly"]
    assert "first point at length" not in texts  # merged away, not kept verbatim
    lists = [b for b in chapter.blocks if isinstance(b, ListBlock)]
    assert [b.items for b in lists] == [["first", "second"]]


async def test_dropping_the_list_still_degrades_that_run() -> None:
    """Merging prose is licensed; losing a list is not, address or no address."""
    reply = json.dumps(
        {
            "texts": {
                "1": [{"block": 1, "type": "paragraph", "text": "smoothed intro"}],
                "2": [{"block": 1, "type": "paragraph", "text": "everything, including the list"}],
            }
        }
    )
    provider = QueueProvider([reply])
    out = await Synthesizer(provider, "m", target_ratio=0.50).synthesize(_mixed_run_chapter())

    chapter = out[0]
    assert chapter.degraded_runs == 1
    lists = [b for b in chapter.blocks if isinstance(b, ListBlock)]
    assert [b.items for b in lists] == [["first", "second"]]  # survived intact


async def test_every_run_unreadable_still_retries() -> None:
    """A response nothing can be read from is a real failure, and does buy a retry."""
    # Run 1 gets entries that are not paragraphs; run 2 gets a flat string where its
    # list demands an array. Neither is readable, so the reply is worth nothing.
    useless = json.dumps({"texts": {"1": [{"bad": 1}], "2": "flat prose"}})
    provider = QueueProvider([useless])
    out = await Synthesizer(provider, "m", target_ratio=0.50, max_retries=2).synthesize(
        _two_run_chapter()
    )

    assert provider.calls == 2
    assert out[0].synthesis_failed is True


async def test_failed_synthesis_is_not_cached_and_retried(tmp_path: Path) -> None:
    cp_path = tmp_path / "run.jsonl"
    # Smooth pass keeps returning malformed JSON → synthesis_failed, must not be recorded.
    out1 = await Synthesizer(QueueProvider(["not json"]), "m", max_retries=1).synthesize(
        _two_chunk_chapter(), checkpoint=CheckpointManager(cp_path)
    )
    assert out1[0].synthesis_failed is True
    assert not CheckpointManager(cp_path).is_done("syn:0")  # failure not cached

    # Resume retries and can now succeed.
    good = QueueProvider([_texts("smoothed intro", "smoothed outro")])
    retried = Synthesizer(good, "m")
    out2 = await retried.synthesize(_two_chunk_chapter(), checkpoint=CheckpointManager(cp_path))
    assert good.calls == 1
    assert retried.reused_chapters == 0
    assert out2[0].synthesis_failed is False


async def test_corrupt_synthesis_payload_is_recomputed(tmp_path: Path) -> None:
    cp_path = tmp_path / "run.jsonl"
    reply = _texts("smoothed intro", "smoothed outro")
    # Prime a real record, then corrupt its inner "chapter" payload while keeping the hash.
    await Synthesizer(QueueProvider([reply]), "m").synthesize(
        _two_chunk_chapter(), checkpoint=CheckpointManager(cp_path)
    )
    cp = CheckpointManager(cp_path)
    good_hash = cp.get("syn:0")["source_hash"]  # type: ignore[index]
    cp.record("syn:0", {"source_hash": good_hash, "chapter": {"not": "a chapter"}})

    other = QueueProvider([reply])
    s = Synthesizer(other, "m")
    await s.synthesize(_two_chunk_chapter(), checkpoint=CheckpointManager(cp_path))
    assert other.calls == 1  # validation fails → recompute
    assert s.reused_chapters == 0


async def test_synthesis_checkpoint_key_is_namespaced(tmp_path: Path) -> None:
    cp_path = tmp_path / "run.jsonl"
    await Synthesizer(QueueProvider([_texts("a", "b")]), "m").synthesize(
        _two_chunk_chapter(), checkpoint=CheckpointManager(cp_path)
    )
    keys = set(CheckpointManager(cp_path).results())
    assert keys == {"syn:0"}  # namespaced, never a bare positional id


def _structured_texts(blocks: list) -> str:
    payload = [
        {"type": "paragraph", "text": "Smoothed intro."},
        {"type": "list", "items": ["Condensed a.", "Condensed b."]},
        {"type": "quote", "text": "Smoothed quote."},
        {"type": "paragraph", "text": "Smoothed outro."},
    ]
    return json.dumps({"texts": {"1": payload}})


async def test_synthesizer_preserves_list_and_quote_structure() -> None:
    chunks = [
        _cc(
            [
                ParagraphBlock(text="intro filler words here"),
                ListBlock(items=["alpha detail", "beta detail"], ordered=False),
                QuoteBlock(text="quoted material here"),
            ],
            input_tokens=40,
        ),
        _cc([ParagraphBlock(text="outro filler words here")], input_tokens=40),
    ]
    provider = QueueProvider([_structured_texts([])])
    chapters = await Synthesizer(provider, "m").synthesize(chunks)

    assert len(chapters) == 1
    assert provider.calls == 1
    kinds = [b.type for b in chapters[0].blocks]
    assert kinds == ["paragraph", "list", "quote", "paragraph"]
    lst = chapters[0].blocks[1]
    assert isinstance(lst, ListBlock) and lst.items == ["Condensed a.", "Condensed b."]
    assert isinstance(chapters[0].blocks[2], QuoteBlock)
