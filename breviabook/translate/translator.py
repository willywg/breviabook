"""Integrated translation of the IR (ROADMAP §7.5, §10).

Runs after condensation/synthesis (condense+translate) or directly on the parsed document
(translate-only). Only natural-language units are translated — chapter titles, headings,
paragraphs, quotes, and list items; code, tables, and images are preserved. Identifiers/URLs/
paths inside prose are kept by instruction. Batches of ~40 units per LLM call; unanswered
ids fall back to the original text. Uses the same provider as condensation, so usage/cost
accrues automatically.

When a :class:`~breviabook.persistence.checkpoint.CheckpointManager` is provided
(translate-only mode), completed batches are persisted with a source-language+glossary hash
so a resumed run reuses them instead of re-translating.
"""

from __future__ import annotations

import hashlib
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
from breviabook.persistence.checkpoint import CheckpointManager
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
        checkpoint: CheckpointManager | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.target_lang = target_lang
        self.source_lang = source_lang
        self.glossary = glossary
        self.max_units_per_batch = max_units_per_batch
        self.max_retries = max_retries
        self.checkpoint = checkpoint
        self.untranslated_units = 0
        self.reused_batches = 0

    async def translate_document(
        self,
        doc: Document,
        *,
        on_progress: Callable[[Chapter], None] | None = None,
    ) -> Document:
        chapters: list[Chapter] = []
        for idx, ch in enumerate(doc.chapters):
            chapters.append(await self.translate_chapter(ch, chapter_index=idx))
            if on_progress is not None:
                on_progress(chapters[-1])
        return Document(metadata=doc.metadata, images=doc.images, chapters=chapters)

    async def translate_chapter(self, chapter: Chapter, *, chapter_index: int = 0) -> Chapter:
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

        translations = await self._translate_batched(units, chapter_index)
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

    async def _translate_batched(
        self, units: dict[str, str], chapter_index: int = 0
    ) -> dict[str, str]:
        items = list(units.items())
        out: dict[str, str] = {}
        for start in range(0, len(items), self.max_units_per_batch):
            batch = dict(items[start : start + self.max_units_per_batch])
            cp = self.checkpoint
            key = f"tr:{chapter_index}:{start}"
            source_hash = _batch_fingerprint(
                batch, self.target_lang, self.source_lang, self.glossary
            )
            if cp is not None:
                payload = cp.get(key)
                if payload is not None and payload.get("source_hash") == source_hash:
                    cached = payload.get("translations")
                    if isinstance(cached, dict):
                        out.update(
                            {str(k): str(v) for k, v in cached.items() if isinstance(v, str)}
                        )
                        self.reused_batches += 1
                        continue
            translated = await self._translate_batch_resilient(batch)
            out.update(translated)
            self.untranslated_units += len(batch) - len(translated)
            if cp is not None and translated:
                cp.record(key, {"source_hash": source_hash, "translations": translated})
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


def _batch_fingerprint(
    batch: dict[str, str],
    target_lang: str,
    source_lang: str | None,
    glossary: Glossary | None,
) -> str:
    """SHA-1 over target language, source language, glossary prompt, and batch content.

    This guarantees that switching target language or editing the glossary invalidates
    cached translations — a stale checkpoint from ``--to Spanish`` is never reused for
    ``--to French``.
    """
    h = hashlib.sha1(usedforsecurity=False)
    h.update(target_lang.encode("utf-8"))
    if source_lang:
        h.update(source_lang.encode("utf-8"))
    if glossary and glossary.terms:
        h.update(glossary.prompt_block().encode("utf-8"))
    for uid, text in sorted(batch.items(), key=lambda x: x[0]):
        h.update(f"{uid}:{text}".encode())
    return h.hexdigest()


def count_translatable_units(doc: Document) -> int:
    """Count prose units that the :class:`Translator` would process.

    Includes chapter titles, headings, paragraphs, quotes, and list items. Must stay
    in sync with :meth:`Translator.translate_chapter` so dry-run estimates cannot drift.
    """
    total = 0
    for chapter in doc.chapters:
        if chapter.title:
            total += 1
        for block in chapter.blocks:
            if isinstance(block, (HeadingBlock, ParagraphBlock, QuoteBlock)):
                total += 1
            elif isinstance(block, ListBlock):
                total += len(block.items)
    return total
