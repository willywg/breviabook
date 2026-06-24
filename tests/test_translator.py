"""Phase 10: translation of the condensed IR (scripted provider)."""

from __future__ import annotations

import json

import pytest

from brevia.ir.models import (
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
from brevia.llm.base import Message
from brevia.translate.glossary import Glossary
from brevia.translate.translator import TranslateError, Translator, build_translate_messages


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


async def test_invalid_json_raises_translate_error() -> None:
    chapter = Chapter(blocks=[ParagraphBlock(text="x")])
    with pytest.raises(TranslateError):
        await Translator(ScriptedProvider("not json"), "m", "Spanish").translate_chapter(chapter)


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
