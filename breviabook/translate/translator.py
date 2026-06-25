"""Integrated translation of the condensed IR (ROADMAP §7.5).

Runs after condensation/synthesis, on the much smaller condensed document. Only natural-
language units are translated — chapter titles, headings, paragraphs, quotes, and list items;
code, tables, and images are preserved. Identifiers/URLs/paths inside prose are kept by
instruction. One LLM call per chapter (id→translation JSON); unanswered ids fall back to the
original text. Uses the same provider as condensation, so usage/cost accrues automatically.
"""

from __future__ import annotations

from collections.abc import Callable

from breviabook.ir.models import (
    Block,
    Chapter,
    Document,
    HeadingBlock,
    ListBlock,
    ParagraphBlock,
    QuoteBlock,
)
from breviabook.llm.base import LLMProvider, Message
from breviabook.translate.glossary import Glossary
from breviabook.utils.jsonx import extract_json_object


class TranslateError(Exception):
    """Raised when a translation response cannot be parsed."""


def build_translate_messages(
    units: dict[str, str],
    target_lang: str,
    source_lang: str | None,
    glossary: Glossary | None,
) -> list[Message]:
    src = f"from {source_lang} " if source_lang else ""
    glossary_block = glossary.prompt_block() if glossary else ""
    segments = "\n".join(f"[{uid}] {text}" for uid, text in units.items())
    system = (
        "You are an expert technical translator. You translate prose faithfully while keeping "
        "code, identifiers, API names, file paths, URLs, and numbers exactly as written."
    )
    user = f"""Translate the following numbered text segments {src}to {target_lang}.

Rules:
- Translate only natural-language prose; keep meaning and technical accuracy.
- Preserve identifiers, API names, file paths, URLs, and numbers exactly (do not translate them).
- Do not add or remove segments; translate each one.
{glossary_block}
Return ONLY a JSON object (no prose, no fences) of the form:
{{"translations": {{"1": "<translation of [1]>", "2": "..."}}}}

--- SEGMENTS ---
{segments}"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


class Translator:
    """Translates the text of a condensed :class:`~breviabook.ir.models.Document`."""

    def __init__(
        self,
        provider: LLMProvider,
        model: str,
        target_lang: str,
        *,
        source_lang: str | None = None,
        glossary: Glossary | None = None,
        max_units_per_batch: int = 40,
        max_retries: int = 2,
    ) -> None:
        self.provider = provider
        self.model = model
        self.target_lang = target_lang
        self.source_lang = source_lang
        self.glossary = glossary
        # Translate a chapter in batches: one giant JSON per chapter is fragile (a single
        # malformed string from the model fails the whole chapter). Smaller batches isolate
        # failures, and a failed batch falls back to the source text instead of crashing.
        self.max_units_per_batch = max_units_per_batch
        self.max_retries = max_retries
        self.untranslated_units = 0  # segments left in the source language after fallback

    async def translate_document(
        self,
        doc: Document,
        *,
        on_progress: Callable[[Chapter], None] | None = None,
    ) -> Document:
        chapters: list[Chapter] = []
        for ch in doc.chapters:
            chapters.append(await self.translate_chapter(ch))
            if on_progress is not None:
                on_progress(chapters[-1])
        return Document(metadata=doc.metadata, images=doc.images, chapters=chapters)

    async def translate_chapter(self, chapter: Chapter) -> Chapter:
        units: dict[str, str] = {}
        counter = 0

        def add(text: str) -> str:
            nonlocal counter
            counter += 1
            uid = str(counter)
            units[uid] = text
            return uid

        title_uid = add(chapter.title) if chapter.title else None
        plan: list[tuple[str, Block, str | list[str] | None]] = []
        for block in chapter.blocks:
            if isinstance(block, (HeadingBlock, ParagraphBlock, QuoteBlock)):
                plan.append(("text", block, add(block.text)))
            elif isinstance(block, ListBlock):
                plan.append(("list", block, [add(item) for item in block.items]))
            else:
                plan.append(("keep", block, None))

        if not units:
            return chapter

        translations = await self._translate_batched(units)
        new_blocks: list[Block] = []
        for kind, block, ref in plan:
            if kind == "text" and isinstance(ref, str):
                text = self._original_text(block)
                new_blocks.append(block.model_copy(update={"text": translations.get(ref, text)}))
            elif kind == "list" and isinstance(ref, list) and isinstance(block, ListBlock):
                items = [
                    translations.get(uid, orig) for uid, orig in zip(ref, block.items, strict=True)
                ]
                new_blocks.append(block.model_copy(update={"items": items}))
            else:
                new_blocks.append(block)

        new_title = chapter.title
        if title_uid is not None and chapter.title is not None:
            new_title = translations.get(title_uid, chapter.title)
        return Chapter(title=new_title, blocks=new_blocks)

    async def _translate_batched(self, units: dict[str, str]) -> dict[str, str]:
        """Translate units in bounded batches; a batch that keeps failing is left untranslated."""
        items = list(units.items())
        out: dict[str, str] = {}
        for start in range(0, len(items), self.max_units_per_batch):
            batch = dict(items[start : start + self.max_units_per_batch])
            translated = await self._translate_batch_resilient(batch)
            out.update(translated)
            self.untranslated_units += len(batch) - len(translated)
        return out

    async def _translate_batch_resilient(self, batch: dict[str, str]) -> dict[str, str]:
        last_error: TranslateError | None = None
        for _attempt in range(self.max_retries):
            try:
                return await self._translate_units(batch)
            except TranslateError as exc:
                last_error = exc
        # Give up on this batch: callers fall back to the original text for missing ids.
        assert last_error is not None
        return {}

    async def _translate_units(self, units: dict[str, str]) -> dict[str, str]:
        messages = build_translate_messages(
            units, self.target_lang, self.source_lang, self.glossary
        )
        raw = await self.provider.generate(messages, self.model)
        try:
            obj = extract_json_object(raw)
        except ValueError as exc:
            raise TranslateError(str(exc)) from exc
        translations_raw = obj.get("translations")
        result: dict[str, str] = {}
        if isinstance(translations_raw, dict):
            for key, value in translations_raw.items():
                if isinstance(value, str):
                    result[str(key)] = value
        return result

    @staticmethod
    def _original_text(block: Block) -> str:
        return getattr(block, "text", "")
