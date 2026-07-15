"""Phase 10: translation of the condensed IR (scripted provider)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from breviabook.ir.models import (
    Chapter,
    CodeBlock,
    Document,
    DocumentMetadata,
    HeadingBlock,
    ImageAsset,
    ImageBlock,
    ListBlock,
    ParagraphBlock,
    TableBlock,
)
from breviabook.llm.base import Message
from breviabook.persistence.checkpoint import CheckpointManager
from breviabook.translate.glossary import Glossary
from breviabook.translate.translator import (
    TranslateError,
    Translator,
    _batch_fingerprint,
    build_translate_messages,
    count_translatable_units,
)


class ScriptedProvider:
    name = "scripted"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.last_prompt = ""
        self.calls = 0

    async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
        self.calls += 1
        self.last_prompt = messages[-1]["content"]
        return self.reply


def _translations(mapping: dict[str, str]) -> str:
    return json.dumps({"translations": mapping})


async def test_translates_text_blocks_and_preserves_code_table_image() -> None:
    chapter = Chapter(
        title="Chapter One",
        blocks=[
            HeadingBlock(level=1, text="Hello"),
            ParagraphBlock(text="A paragraph."),
            CodeBlock(language="python", text="x = 1\n"),
            ListBlock(items=["first", "second"]),
            TableBlock(rows=[["a", "b"]]),
            ImageBlock(image_id="img1", caption="cap"),
        ],
    )
    # units: 1=title, 2=Hello, 3=paragraph, 4=first, 5=second
    reply = _translations(
        {"1": "Capítulo Uno", "2": "Hola", "3": "Un párrafo.", "4": "primero", "5": "segundo"}
    )
    provider = ScriptedProvider(reply)
    out = await Translator(provider, "m", "Spanish").translate_chapter(chapter)

    assert out.title == "Capítulo Uno"
    assert isinstance(out.blocks[0], HeadingBlock) and out.blocks[0].text == "Hola"
    assert out.blocks[0].level == 1  # type/level preserved
    assert isinstance(out.blocks[1], ParagraphBlock) and out.blocks[1].text == "Un párrafo."
    # code preserved verbatim, never translated
    assert isinstance(out.blocks[2], CodeBlock) and out.blocks[2].text == "x = 1\n"
    assert isinstance(out.blocks[3], ListBlock) and out.blocks[3].items == ["primero", "segundo"]
    assert isinstance(out.blocks[4], TableBlock) and out.blocks[4].rows == [["a", "b"]]
    assert isinstance(out.blocks[5], ImageBlock) and out.blocks[5].image_id == "img1"


async def test_code_text_not_sent_to_llm() -> None:
    chapter = Chapter(blocks=[ParagraphBlock(text="hola"), CodeBlock(text="SECRET_CODE_TOKEN")])
    provider = ScriptedProvider(_translations({"1": "hi"}))
    await Translator(provider, "m", "English").translate_chapter(chapter)
    assert "SECRET_CODE_TOKEN" not in provider.last_prompt


async def test_missing_translation_falls_back_to_original() -> None:
    chapter = Chapter(blocks=[ParagraphBlock(text="keepme"), ParagraphBlock(text="alsokeep")])
    provider = ScriptedProvider(_translations({"1": "traducido"}))  # id 2 missing
    out = await Translator(provider, "m", "Spanish").translate_chapter(chapter)
    assert out.blocks[0].text == "traducido"  # type: ignore[union-attr]
    assert out.blocks[1].text == "alsokeep"  # type: ignore[union-attr]


async def test_code_only_chapter_makes_no_call() -> None:
    chapter = Chapter(blocks=[CodeBlock(text="x = 1\n")])
    provider = ScriptedProvider(_translations({}))
    out = await Translator(provider, "m", "Spanish").translate_chapter(chapter)
    assert provider.calls == 0
    assert out.blocks[0].text == "x = 1\n"  # type: ignore[union-attr]


async def test_glossary_appears_in_prompt() -> None:
    chapter = Chapter(blocks=[ParagraphBlock(text="a thread")])
    provider = ScriptedProvider(_translations({"1": "un hilo"}))
    glossary = Glossary({"thread": "hilo"})
    await Translator(provider, "m", "Spanish", glossary=glossary).translate_chapter(chapter)
    assert "thread → hilo" in provider.last_prompt


def test_build_messages_includes_source_lang() -> None:
    messages = build_translate_messages({"1": "x"}, "Spanish", "English", None)
    assert "from English to Spanish" in messages[1]["content"]


async def test_invalid_json_falls_back_to_source_without_crashing() -> None:
    # A malformed model response must not crash the run: the segment stays in the source
    # language and is counted, rather than raising (resilient batching).
    chapter = Chapter(blocks=[ParagraphBlock(text="keepme")])
    provider = ScriptedProvider("not json")
    translator = Translator(provider, "m", "Spanish", max_retries=2)
    out = await translator.translate_chapter(chapter)
    assert out.blocks[0].text == "keepme"  # type: ignore[union-attr]
    assert translator.untranslated_units == 1
    assert provider.calls == 2  # retried before giving up


async def test_translate_units_still_raises_on_invalid_json() -> None:
    # The low-level call still surfaces the error; resilience lives in the batching layer.
    with pytest.raises(TranslateError):
        await Translator(ScriptedProvider("not json"), "m", "Spanish")._translate_units({"1": "x"})


class BatchCountingProvider:
    name = "batch"

    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
        content = messages[-1]["content"]
        ids = [ln for ln in content.splitlines() if ln.strip().startswith("[")]
        self.batch_sizes.append(len(ids))
        return _translations({str(i): f"t{i}" for i in range(1, 200)})


async def test_units_are_translated_in_bounded_batches() -> None:
    chapter = Chapter(blocks=[ParagraphBlock(text=f"p{i}") for i in range(100)])
    provider = BatchCountingProvider()
    out = await Translator(provider, "m", "Spanish", max_units_per_batch=40).translate_chapter(
        chapter
    )
    assert len(provider.batch_sizes) == 3  # 100 units -> 40 + 40 + 20
    assert max(provider.batch_sizes) <= 40
    assert out.blocks[0].text == "t1"  # type: ignore[union-attr]


async def test_translate_document_keeps_images_and_metadata() -> None:
    doc = Document(
        metadata=DocumentMetadata(title="T", source_format="epub"),
        images={"i": ImageAsset(image_id="i", data=b"\x89PNG", mime="image/png")},
        chapters=[Chapter(blocks=[ParagraphBlock(text="hola")])],
    )
    provider = ScriptedProvider(_translations({"1": "hello"}))
    out = await Translator(provider, "m", "English").translate_document(doc)
    assert set(out.images) == {"i"}
    assert out.metadata.title == "T"
    assert out.chapters[0].blocks[0].text == "hello"  # type: ignore[union-attr]


# --------------------------------------------------------------------------- #
# Checkpoint tests (translate-only — feat/translate-command)
# --------------------------------------------------------------------------- #


class CountingProvider:
    name = "counting"

    def __init__(self, prefix: str = "ES") -> None:
        self.prefix = prefix
        self.calls = 0

    async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
        self.calls += 1
        content = messages[-1]["content"]
        ids = []
        for ln in content.splitlines():
            stripped = ln.strip()
            if stripped.startswith("[") and "]" in stripped:
                ids.append(stripped[1 : stripped.index("]")])
        return _translations({uid: f"{self.prefix}{uid}" for uid in ids})


async def test_checkpoint_reuses_completed_batches(tmp_path: Path) -> None:
    cp_path = tmp_path / "checkpoint.jsonl"
    cp = CheckpointManager(cp_path)

    # First run: translate 80 units (2 batches of 40).
    chapter = Chapter(blocks=[ParagraphBlock(text=f"p{i}") for i in range(80)])
    provider1 = CountingProvider("ES")
    t1 = Translator(provider1, "m", "Spanish", max_units_per_batch=40, checkpoint=cp)
    await t1.translate_chapter(chapter)
    assert provider1.calls == 2
    assert t1.reused_batches == 0

    # Second run with same checkpoint: all batches reused.
    cp2 = CheckpointManager(cp_path)  # reload
    provider2 = CountingProvider("ES")
    t2 = Translator(provider2, "m", "Spanish", max_units_per_batch=40, checkpoint=cp2)
    result = await t2.translate_chapter(chapter)
    assert provider2.calls == 0
    assert t2.reused_batches == 2
    assert result.blocks[0].text == "ES1"  # type: ignore[union-attr]


async def test_checkpoint_invalidated_by_language_change(tmp_path: Path) -> None:
    cp_path = tmp_path / "checkpoint.jsonl"
    cp = CheckpointManager(cp_path)

    chapter = Chapter(blocks=[ParagraphBlock(text="hello")])
    t_es = Translator(CountingProvider("ES"), "m", "Spanish", checkpoint=cp)
    await t_es.translate_chapter(chapter)

    # Switch to French — cached Spanish batch must NOT be reused.
    cp2 = CheckpointManager(cp_path)
    provider_fr = CountingProvider("FR")
    t_fr = Translator(provider_fr, "m", "French", checkpoint=cp2)
    result = await t_fr.translate_chapter(chapter)
    assert provider_fr.calls == 1
    assert t_fr.reused_batches == 0
    assert result.blocks[0].text == "FR1"  # type: ignore[union-attr]


async def test_checkpoint_invalidated_by_glossary_change(tmp_path: Path) -> None:
    cp_path = tmp_path / "checkpoint.jsonl"
    cp = CheckpointManager(cp_path)

    chapter = Chapter(blocks=[ParagraphBlock(text="thread")])
    t1 = Translator(
        CountingProvider("ES"),
        "m",
        "Spanish",
        glossary=Glossary({"thread": "hilo"}),
        checkpoint=cp,
    )
    await t1.translate_chapter(chapter)

    # Same language, different glossary — must re-translate.
    cp2 = CheckpointManager(cp_path)
    provider2 = CountingProvider("ES2")
    t2 = Translator(
        provider2,
        "m",
        "Spanish",
        glossary=Glossary({"thread": "hebra"}),
        checkpoint=cp2,
    )
    await t2.translate_chapter(chapter)
    assert provider2.calls == 1
    assert t2.reused_batches == 0


async def test_failed_batch_not_checkpointed(tmp_path: Path) -> None:
    cp_path = tmp_path / "checkpoint.jsonl"
    cp = CheckpointManager(cp_path)

    # A provider that always fails to parse.
    provider = ScriptedProvider("not json")
    t1 = Translator(provider, "m", "Spanish", max_retries=1, checkpoint=cp)
    await t1.translate_chapter(Chapter(blocks=[ParagraphBlock(text="hello")]))
    assert t1.untranslated_units == 1

    # The checkpoint must be empty — failures are never cached.
    assert len(cp.results()) == 0


class PartialProvider:
    """Answers with valid JSON that is missing the last segment of every batch."""

    name = "partial"

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
        self.calls += 1
        prompt = messages[-1]["content"]
        uids = [line[1 : line.index("]")] for line in prompt.splitlines() if line.startswith("[")]
        return _translations({uid: f"ES-{uid}" for uid in uids[:-1]})  # drops the last one


async def test_partial_batch_not_checkpointed(tmp_path: Path) -> None:
    """A batch answered only in part must not be cached: the gaps must stay retryable.

    Caching it would freeze the missing segments in the source language forever (no --resume
    would retry them) and zero out ``untranslated_units`` on the resumed run, so a run with
    untranslated text would report a clean success.
    """
    cp_path = tmp_path / "checkpoint.jsonl"
    chapter = Chapter(blocks=[ParagraphBlock(text=f"p{i}") for i in (1, 2, 3)])

    provider = PartialProvider()
    t1 = Translator(provider, "m", "Spanish", checkpoint=CheckpointManager(cp_path))
    out1 = await t1.translate_chapter(chapter)

    assert t1.untranslated_units == 1
    assert [b.text for b in out1.blocks] == ["ES-1", "ES-2", "p3"]  # 3rd fell back to source
    assert len(CheckpointManager(cp_path).results()) == 0  # nothing cached

    # Resume with a provider that answers fully: the batch is retried, not served from cache.
    good = CountingProvider("ES")
    t2 = Translator(good, "m", "Spanish", checkpoint=CheckpointManager(cp_path))
    out2 = await t2.translate_chapter(chapter)

    assert good.calls == 1  # re-translated, not reused
    assert t2.reused_batches == 0
    assert t2.untranslated_units == 0
    assert all(b.text.startswith("ES") for b in out2.blocks)  # nothing left in the source language


async def test_response_ids_outside_the_batch_are_ignored() -> None:
    """Only the ids we asked for are accepted; a stray id must not overwrite another unit."""
    chapter = Chapter(blocks=[ParagraphBlock(text="p1"), ParagraphBlock(text="p2")])
    # The model answers our two ids plus "7", which belongs to no unit in this chapter.
    provider = ScriptedProvider(_translations({"1": "uno", "2": "dos", "7": "basura"}))
    t = Translator(provider, "m", "Spanish", checkpoint=None)

    out = await t.translate_chapter(chapter)

    assert [b.text for b in out.blocks] == ["uno", "dos"]
    assert t.untranslated_units == 0  # "7" must not be counted as a translated unit either


async def test_incomplete_checkpoint_record_is_ignored(tmp_path: Path) -> None:
    """A record that matches the fingerprint but misses units is a miss, not a partial hit."""
    cp_path = tmp_path / "checkpoint.jsonl"
    chapter = Chapter(blocks=[ParagraphBlock(text=f"p{i}") for i in (1, 2)])
    units = {"1": "p1", "2": "p2"}

    # Hand-craft a truncated record (e.g. from a torn write) covering only unit "1".
    cp = CheckpointManager(cp_path)
    cp.record(
        "tr:0:0",
        {
            "source_hash": _batch_fingerprint(units, "Spanish", None, None),
            "translations": {"1": "uno"},
        },
    )

    provider = CountingProvider("ES")
    t = Translator(provider, "m", "Spanish", checkpoint=CheckpointManager(cp_path))
    out = await t.translate_chapter(chapter)

    assert provider.calls == 1  # re-translated rather than partially reused
    assert t.reused_batches == 0
    assert [b.text for b in out.blocks] == ["ES1", "ES2"]


async def test_checkpoint_resume_produces_identical_output(tmp_path: Path) -> None:
    cp_path = tmp_path / "checkpoint.jsonl"
    chapter = Chapter(blocks=[ParagraphBlock(text=f"p{i}") for i in range(100)])

    # Full uninterrupted run.
    cp1 = CheckpointManager(cp_path)
    p1 = CountingProvider("ES")
    t1 = Translator(p1, "m", "Spanish", max_units_per_batch=40, checkpoint=cp1)
    out1 = await t1.translate_chapter(chapter)

    # Simulate resume: prime checkpoint with first half of batches completed.
    cp2_path = tmp_path / "checkpoint2.jsonl"
    cp2 = CheckpointManager(cp2_path)
    # Do half the batches via a fresh translator, then resume.
    p2 = CountingProvider("ES")
    t2 = Translator(p2, "m", "Spanish", max_units_per_batch=40, checkpoint=cp2)
    await t2.translate_chapter(chapter)
    first_half_calls = p2.calls

    # Resume: reload checkpoint, use a provider that returns identically.
    cp3 = CheckpointManager(cp2_path)
    p3 = CountingProvider("ES")
    t3 = Translator(p3, "m", "Spanish", max_units_per_batch=40, checkpoint=cp3)
    out3 = await t3.translate_chapter(chapter)
    # On resume, only the not-yet-cached batches are called.
    assert p3.calls == 0  # all were cached from the first pass
    assert t3.reused_batches == first_half_calls

    # Output must be identical to the full run.
    assert out3.title == out1.title
    for a, b in zip(out3.blocks, out1.blocks, strict=True):
        assert a == b


def test_batch_fingerprint_includes_language() -> None:
    batch = {"1": "hello", "2": "world"}
    h1 = _batch_fingerprint(batch, "Spanish", "English", None)
    h2 = _batch_fingerprint(batch, "French", "English", None)
    assert h1 != h2


def test_batch_fingerprint_includes_glossary() -> None:
    batch = {"1": "thread"}
    h1 = _batch_fingerprint(batch, "Spanish", None, Glossary({"thread": "hilo"}))
    h2 = _batch_fingerprint(batch, "Spanish", None, Glossary({"thread": "hebra"}))
    assert h1 != h2


def test_count_translatable_units() -> None:
    doc = Document(
        metadata=DocumentMetadata(title="T", source_format="epub"),
        chapters=[
            Chapter(
                title="Ch1",
                blocks=[
                    ParagraphBlock(text="p1"),
                    CodeBlock(text="code"),
                    ListBlock(items=["a", "b", "c"]),
                    ImageBlock(image_id="i"),
                ],
            ),
            Chapter(
                title=None,
                blocks=[
                    HeadingBlock(level=2, text="H2"),
                    ParagraphBlock(text="p2"),
                ],
            ),
        ],
    )
    # Ch1: title(1) + paragraph(1) + list items(3) = 5
    # Ch2: no title + heading(1) + paragraph(1) = 2
    assert count_translatable_units(doc) == 7


class TagEchoProvider:
    """Translates visible text (uppercase) but keeps inline tags exactly."""

    name = "tag-echo"

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
        import re

        self.calls += 1
        out: dict[str, str] = {}
        for ln in messages[-1]["content"].splitlines():
            m = re.match(r"\[(\d+)\]\s(.*)", ln)
            if not m:
                continue
            uid, seg = m.group(1), m.group(2)
            seg = re.sub(r">([^<]+)<", lambda x: ">" + x.group(1).upper() + "<", seg)
            seg = re.sub(r"^([^<>]+)", lambda x: x.group(1).upper(), seg)
            out[uid] = seg
        return _translations(out)


class TagManglingProvider:
    """Returns translated text but drops the tags entirely (a hostile/garbled reply)."""

    name = "tag-mangle"

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
        import re

        self.calls += 1
        out: dict[str, str] = {}
        for ln in messages[-1]["content"].splitlines():
            m = re.match(r"\[(\d+)\]\s(.*)", ln)
            if m:
                out[m.group(1)] = re.sub(r"<[^>]+>", "", m.group(2)).upper()  # strip all tags
        return _translations(out)


async def test_rich_tags_preserved_through_translation() -> None:
    rich = '<span style="color:#9e0b0f"><strong>Guiding</strong></span>'
    chapter = Chapter(blocks=[HeadingBlock(level=2, text="Guiding", rich=rich)])
    t = Translator(TagEchoProvider(), "m", "Spanish")
    out = await t.translate_chapter(chapter)
    block = out.blocks[0]
    assert block.text == "GUIDING"  # visible text translated
    assert block.rich == '<span style="color:#9e0b0f"><strong>GUIDING</strong></span>'
    assert t.rich_downgraded == 0


async def test_mangled_tags_downgrade_to_translated_plain() -> None:
    rich = '<a href="https://x.com">link</a> and <em>really</em>'
    chapter = Chapter(blocks=[ParagraphBlock(text="link and really", rich=rich)])
    t = Translator(TagManglingProvider(), "m", "Spanish")
    out = await t.translate_chapter(chapter)
    block = out.blocks[0]
    assert block.rich is None  # styling dropped rather than misattributed
    assert block.text == "LINK AND REALLY"  # but the translated text is kept, not the English
    assert t.rich_downgraded == 1


async def test_plain_blocks_unaffected_by_rich_path() -> None:
    chapter = Chapter(blocks=[ParagraphBlock(text="hello")])
    t = Translator(CountingProvider("ES"), "m", "Spanish")
    out = await t.translate_chapter(chapter)
    assert out.blocks[0].text == "ES1"
    assert out.blocks[0].rich is None
    assert t.rich_downgraded == 0


class PoisonBatchProvider:
    """Fails to produce valid JSON whenever a specific 'poison' segment is in the batch."""

    name = "poison"

    def __init__(self, poison_text: str) -> None:
        self.poison_text = poison_text
        self.calls = 0

    async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
        import re

        self.calls += 1
        content = messages[-1]["content"]
        if self.poison_text in content:
            return "}{ not json at all"  # the poison segment breaks the whole reply
        out = {}
        for ln in content.splitlines():
            m = re.match(r"\[(\d+)\]\s(.*)", ln)
            if m:
                out[m.group(1)] = f"ES-{m.group(1)}"
        return _translations(out)


async def test_bisection_isolates_poison_segment() -> None:
    # 8 units in one batch; unit 5 ("boom") makes any batch containing it return invalid JSON.
    items = [ParagraphBlock(text=f"seg{i}") for i in range(1, 9)]
    items[4] = ParagraphBlock(text="boom")
    chapter = Chapter(blocks=items)

    provider = PoisonBatchProvider("boom")
    t = Translator(provider, "m", "Spanish", max_units_per_batch=40, max_retries=1)
    out = await t.translate_chapter(chapter)

    texts = [b.text for b in out.blocks]
    # Only the poison neighbour stays in source; all 7 others are recovered in this one run.
    assert texts[4] == "boom"  # untranslated (fell back to source)
    assert t.untranslated_units == 1
    assert all(txt.startswith("ES-") for i, txt in enumerate(texts) if i != 4)
