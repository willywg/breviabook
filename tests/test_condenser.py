"""Phase 4: per-chunk condensation with a scripted mock provider (no real LLM)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from breviabook.condense.chunker import Chunker
from breviabook.condense.condenser import (
    CondensedChunk,
    CondenseError,
    Condenser,
    assemble_condensed_document,
)
from breviabook.ir.models import (
    Chapter,
    CodeBlock,
    Document,
    DocumentMetadata,
    ImageAsset,
    ImageBlock,
    ParagraphBlock,
)
from breviabook.llm.base import Message
from breviabook.llm.usage import Usage
from breviabook.persistence.checkpoint import CheckpointManager


class ScriptedProvider:
    """Returns a canned reply and records every call (deterministic)."""

    name = "scripted"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0
        self.usage = Usage()

    async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
        self.calls += 1
        return self.reply


class BoomProvider:
    """Fails if ever called — proves a code path makes no LLM call."""

    name = "boom"
    usage = Usage()

    async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
        raise AssertionError("provider should not be called")


def _chunk(blocks: list, *, idx: int = 0, title: str = "A"):
    doc = Document(
        metadata=DocumentMetadata(title="T", source_format="epub"),
        chapters=[Chapter(title=title, blocks=blocks)],
    )
    return Chunker(max_tokens=100_000).chunk(doc)[0]


async def test_condense_preserves_code_and_order() -> None:
    chunk = _chunk(
        [
            ParagraphBlock(text="A long intro paragraph with filler."),
            CodeBlock(language="python", text="def f():\n    return 1\n"),
            ParagraphBlock(text="A closing paragraph with more filler."),
            ImageBlock(image_id="fig1", caption="diagram"),
        ]
    )
    reply = json.dumps(
        {"texts": {"1": "Short intro.", "2": "Short close."}, "essential_images": ["fig1"]}
    )
    cc = await Condenser(ScriptedProvider(reply), "m").condense_chunk(chunk)

    kinds = [b.type for b in cc.blocks]
    assert kinds == ["paragraph", "code", "paragraph", "image"]
    assert isinstance(cc.blocks[0], ParagraphBlock) and cc.blocks[0].text == "Short intro."
    code = cc.blocks[1]
    assert isinstance(code, CodeBlock) and code.text == "def f():\n    return 1\n"
    assert cc.kept_image_ids == ["fig1"]
    assert cc.dropped_image_ids == []


async def test_image_dropped_when_not_essential() -> None:
    chunk = _chunk([ParagraphBlock(text="text"), ImageBlock(image_id="fig1", caption="decorative")])
    reply = json.dumps({"texts": {"1": "t"}, "essential_images": []})
    cc = await Condenser(ScriptedProvider(reply), "m").condense_chunk(chunk)
    assert cc.kept_image_ids == []
    assert cc.dropped_image_ids == ["fig1"]
    assert all(b.type != "image" for b in cc.blocks)


async def test_output_longer_than_input_flagged() -> None:
    chunk = _chunk([ParagraphBlock(text="short")])
    reply = json.dumps({"texts": {"1": "much longer condensed text " * 20}, "essential_images": []})
    cc = await Condenser(ScriptedProvider(reply), "m").condense_chunk(chunk)
    assert cc.output_longer_than_input is True


async def test_code_only_chunk_passthrough_no_llm_call() -> None:
    chunk = _chunk([CodeBlock(language="python", text="x = 1\n")])
    cc = await Condenser(BoomProvider(), "m").condense_chunk(chunk)
    assert len(cc.blocks) == 1
    assert isinstance(cc.blocks[0], CodeBlock)
    assert cc.output_longer_than_input is False


async def test_json_in_fences_is_tolerated() -> None:
    chunk = _chunk([ParagraphBlock(text="filler text here")])
    reply = '```json\n{"texts": {"1": "ok"}, "essential_images": []}\n```'
    cc = await Condenser(ScriptedProvider(reply), "m").condense_chunk(chunk)
    assert any(getattr(b, "text", None) == "ok" for b in cc.blocks)


async def test_invalid_json_keeps_chunk_uncondensed_without_crashing() -> None:
    # Persistent malformed JSON must not crash the run: keep the original text, flag it,
    # and retry max_retries times first.
    chunk = _chunk([ParagraphBlock(text="filler text that should survive")])
    provider = ScriptedProvider("not json at all")
    cc = await Condenser(provider, "m", max_retries=3).condense_chunk(chunk)
    assert cc.condense_failed is True
    assert provider.calls == 3  # retried before giving up
    assert any(getattr(b, "text", None) == "filler text that should survive" for b in cc.blocks)


async def test_low_level_parse_still_raises() -> None:
    from breviabook.condense.condenser import _parse_response

    with pytest.raises(CondenseError):
        _parse_response("not json at all")


class FlakyProvider:
    """Returns bad JSON for the first N calls, then a valid reply."""

    name = "flaky"

    def __init__(self, bad: int, good_reply: str) -> None:
        self.bad = bad
        self.good_reply = good_reply
        self.calls = 0
        self.usage = Usage()

    async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
        self.calls += 1
        return "broken" if self.calls <= self.bad else self.good_reply


async def test_retry_recovers_from_transient_bad_json() -> None:
    chunk = _chunk([ParagraphBlock(text="please condense me")])
    good = json.dumps({"texts": {"1": "condensed ok"}, "essential_images": []})
    provider = FlakyProvider(bad=1, good_reply=good)
    cc = await Condenser(provider, "m", max_retries=3).condense_chunk(chunk)
    assert cc.condense_failed is False
    assert provider.calls == 2  # one failure, then success
    assert any(getattr(b, "text", None) == "condensed ok" for b in cc.blocks)


async def test_checkpoint_resume_skips_provider(tmp_path: Path) -> None:
    chunk = _chunk([ParagraphBlock(text="some text to condense")])
    reply = json.dumps({"texts": {"1": "done"}, "essential_images": []})
    cp = CheckpointManager(tmp_path / "job.jsonl")

    provider = ScriptedProvider(reply)
    first = await Condenser(provider, "m").condense([chunk], checkpoint=cp)
    assert provider.calls == 1

    # Resume with a provider that explodes if called — must use the checkpoint.
    resumed = await Condenser(BoomProvider(), "m").condense([chunk], checkpoint=cp)
    assert resumed[0].blocks[0].text == first[0].blocks[0].text  # type: ignore[union-attr]


async def test_resume_counts_reused_chunks(tmp_path: Path) -> None:
    chunk = _chunk([ParagraphBlock(text="some text to condense")])
    reply = json.dumps({"texts": {"1": "done"}, "essential_images": []})
    cp = CheckpointManager(tmp_path / "job.jsonl")

    c1 = Condenser(ScriptedProvider(reply), "m")
    await c1.condense([chunk], checkpoint=cp)
    assert c1.reused_chunks == 0

    c2 = Condenser(BoomProvider(), "m")
    await c2.condense([chunk], checkpoint=cp)
    assert c2.reused_chunks == 1


async def test_checkpoint_invalidated_by_model_change(tmp_path: Path) -> None:
    chunk = _chunk([ParagraphBlock(text="some text to condense")])
    reply = json.dumps({"texts": {"1": "done"}, "essential_images": []})
    cp = CheckpointManager(tmp_path / "job.jsonl")
    await Condenser(ScriptedProvider(reply), "model-a").condense([chunk], checkpoint=cp)

    # Same chunk, different model — the cached output was produced by another model.
    provider = ScriptedProvider(reply)
    c2 = Condenser(provider, "model-b")
    await c2.condense([chunk], checkpoint=cp)
    assert provider.calls == 1
    assert c2.reused_chunks == 0


async def test_checkpoint_invalidated_by_ratio_change(tmp_path: Path) -> None:
    chunk = _chunk([ParagraphBlock(text="some text to condense")])
    reply = json.dumps({"texts": {"1": "done"}, "essential_images": []})
    cp = CheckpointManager(tmp_path / "job.jsonl")
    await Condenser(ScriptedProvider(reply), "m", 0.30).condense([chunk], checkpoint=cp)

    provider = ScriptedProvider(reply)
    c2 = Condenser(provider, "m", 0.50)
    await c2.condense([chunk], checkpoint=cp)
    assert provider.calls == 1
    assert c2.reused_chunks == 0


async def test_checkpoint_invalidated_by_content_change(tmp_path: Path) -> None:
    # Chunk ids are positional (ch{i}-{n}), so a different chunking (--chunk-tokens) or a
    # different book under the same input stem reuses ids — only the content hash tells
    # them apart.
    reply = json.dumps({"texts": {"1": "done"}, "essential_images": []})
    cp = CheckpointManager(tmp_path / "job.jsonl")
    first_chunk = _chunk([ParagraphBlock(text="original book text")])
    await Condenser(ScriptedProvider(reply), "m").condense([first_chunk], checkpoint=cp)

    other_chunk = _chunk([ParagraphBlock(text="a different book, same positional id")])
    assert other_chunk.id == first_chunk.id
    provider = ScriptedProvider(reply)
    c2 = Condenser(provider, "m")
    await c2.condense([other_chunk], checkpoint=cp)
    assert provider.calls == 1
    assert c2.reused_chunks == 0


async def test_old_bare_format_record_is_recomputed(tmp_path: Path) -> None:
    # Records written before fingerprints existed hold the CondensedChunk dump directly
    # (no source_hash). They must be recomputed once, not crash and not be reused.
    chunk = _chunk([ParagraphBlock(text="some text to condense")])
    reply = json.dumps({"texts": {"1": "done"}, "essential_images": []})
    cp = CheckpointManager(tmp_path / "job.jsonl")
    legacy = await Condenser(ScriptedProvider(reply), "m").condense_chunk(chunk)
    cp.record(chunk.id, legacy.model_dump(mode="json"))

    provider = ScriptedProvider(reply)
    c2 = Condenser(provider, "m")
    await c2.condense([chunk], checkpoint=cp)
    assert provider.calls == 1
    assert c2.reused_chunks == 0
    # The record has been rewritten in the fingerprinted shape.
    assert "source_hash" in cp.get(chunk.id)  # type: ignore[operator]


async def test_failed_chunk_not_checkpointed_and_retried_on_resume(tmp_path: Path) -> None:
    chunk = _chunk([ParagraphBlock(text="please condense me")])
    cp = CheckpointManager(tmp_path / "job.jsonl")

    c1 = Condenser(ScriptedProvider("not json at all"), "m", max_retries=2)
    failed = await c1.condense([chunk], checkpoint=cp)
    assert failed[0].condense_failed is True
    assert not cp.is_done(chunk.id)  # failures are never cached

    # A resume is the chance to retry the transient failure.
    good = json.dumps({"texts": {"1": "condensed ok"}, "essential_images": []})
    provider = ScriptedProvider(good)
    c2 = Condenser(provider, "m")
    retried = await c2.condense([chunk], checkpoint=cp)
    assert provider.calls == 1
    assert retried[0].condense_failed is False
    assert cp.is_done(chunk.id)


async def test_corrupt_inner_payload_is_recomputed(tmp_path: Path) -> None:
    # A record whose hash matches but whose chunk payload is broken (torn write, hand edit)
    # is treated as stale, not as a hit.
    from breviabook.condense.condenser import _chunk_fingerprint

    chunk = _chunk([ParagraphBlock(text="some text to condense")])
    cp = CheckpointManager(tmp_path / "job.jsonl")
    cp.record(
        chunk.id,
        {
            "source_hash": _chunk_fingerprint(chunk, "m", 0.30),
            "chunk": {"id": chunk.id},  # missing required fields
        },
    )

    reply = json.dumps({"texts": {"1": "done"}, "essential_images": []})
    provider = ScriptedProvider(reply)
    c = Condenser(provider, "m")
    await c.condense([chunk], checkpoint=cp)
    assert provider.calls == 1
    assert c.reused_chunks == 0


def test_chunk_fingerprint_is_order_sensitive() -> None:
    from breviabook.condense.condenser import _chunk_fingerprint

    a = ParagraphBlock(text="alpha")
    b = ParagraphBlock(text="beta")
    assert _chunk_fingerprint(_chunk([a, b]), "m", 0.3) != _chunk_fingerprint(
        _chunk([b, a]), "m", 0.3
    )


async def test_assemble_condensed_document_groups_and_filters_images() -> None:
    condensed = [
        CondensedChunk(
            id="ch0-1",
            chapter_index=0,
            chapter_title="One",
            blocks=[ParagraphBlock(text="a"), ImageBlock(image_id="keep1")],
            kept_image_ids=["keep1"],
        ),
        CondensedChunk(
            id="ch1-1",
            chapter_index=1,
            chapter_title="Two",
            blocks=[ParagraphBlock(text="b")],
        ),
    ]
    original = Document(
        metadata=DocumentMetadata(title="T", source_format="epub"),
        images={
            "keep1": ImageAsset(image_id="keep1", data=b"\x89PNG", mime="image/png"),
            "drop1": ImageAsset(image_id="drop1", data=b"\x89PNG", mime="image/png"),
        },
    )
    doc = assemble_condensed_document(original, condensed)
    assert [c.title for c in doc.chapters] == ["One", "Two"]
    assert set(doc.images) == {"keep1"}  # only kept images survive


async def test_condenses_real_fixture_chunks() -> None:
    from breviabook.parsers.epub_parser import EpubParser

    doc = EpubParser().parse(Path(__file__).parent / "fixtures" / "sample.epub")
    chunks = Chunker(max_tokens=2000).chunk(doc)
    reply = json.dumps({"texts": {"1": "condensed"}, "essential_images": ["fig1"]})
    condensed = await Condenser(ScriptedProvider(reply), "m").condense(chunks)
    assert len(condensed) == len(chunks)
    out = assemble_condensed_document(doc, condensed)
    assert out.chapters
