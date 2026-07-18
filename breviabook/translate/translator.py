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

import asyncio
from collections.abc import Callable

from breviabook.config import DEFAULT_CONCURRENCY
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
from breviabook.persistence.fingerprint import Fingerprint
from breviabook.translate.glossary import Glossary
from breviabook.utils.htmlsan import inline_tag_signature, sanitize_inline, strip_tags
from breviabook.utils.jsonx import extract_json_object
from breviabook.utils.langcodes import to_bcp47


class TranslateError(Exception):
    """Raised when a translation response cannot be parsed."""


# Units per LLM call. One giant JSON per chapter is fragile (a single malformed string fails the
# whole chapter); smaller batches isolate failures. The dry-run estimate reads this too, so the
# estimated call count cannot drift from the real one.
DEFAULT_UNITS_PER_BATCH = 40


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
- Some segments contain inline HTML tags (<em>, <strong>, <a>, <code>, <span>, <sup>, <sub>, <s>,
  <br/>). Keep every tag and its attributes exactly as written; translate only the visible text
  between tags. Do not add, remove, reorder, or re-nest tags. Preserve <br/> line breaks.
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
        max_units_per_batch: int = DEFAULT_UNITS_PER_BATCH,
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
        # Segments whose translation diverged from the source's inline tags: we keep the translated
        # text but drop styling for that one segment (never emit misattributed markup).
        self.rich_downgraded = 0

    async def translate_document(
        self,
        doc: Document,
        *,
        concurrency: int = DEFAULT_CONCURRENCY,
        on_progress: Callable[[Chapter], None] | None = None,
    ) -> Document:
        """Translate chapters concurrently while retaining their document order."""
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        semaphore = asyncio.Semaphore(concurrency)

        async def translate_one(index: int, chapter: Chapter) -> Chapter:
            async with semaphore:
                translated = await self.translate_chapter(chapter, chapter_index=index)
            if on_progress is not None:
                on_progress(translated)
            return translated

        # gather preserves the order of doc.chapters, not provider completion order.
        chapters = await asyncio.gather(
            *(translate_one(index, chapter) for index, chapter in enumerate(doc.chapters))
        )
        # EPUB dc:language / PDF html lang must reflect the target, not the source (F5).
        meta = doc.metadata.model_copy(update={"language": to_bcp47(self.target_lang)})
        return Document(metadata=meta, images=doc.images, chapters=list(chapters))

    async def translate_chapter(self, chapter: Chapter, *, chapter_index: int = 0) -> Chapter:
        units: dict[str, str] = {}
        # uid -> source rich (inline HTML) when the segment carries markup, else None.
        source_rich: dict[str, str | None] = {}
        counter = 0

        def add(text: str, rich: str | None = None) -> str:
            nonlocal counter
            counter += 1
            uid = str(counter)
            units[uid] = rich if rich is not None else text  # send the styled form when present
            source_rich[uid] = rich
            return uid

        title_uid = add(chapter.title) if chapter.title else None
        plan: list[tuple[str, Block, str | list[str] | None]] = []
        for block in chapter.blocks:
            if isinstance(block, (HeadingBlock, ParagraphBlock, QuoteBlock)):
                plan.append(("text", block, add(block.text, block.rich)))
            elif isinstance(block, ListBlock):
                src_riches: list[str | None] = (
                    list(block.items_rich)
                    if block.items_rich is not None
                    else [None] * len(block.items)
                )
                uids = [add(item, r) for item, r in zip(block.items, src_riches, strict=True)]
                plan.append(("list", block, uids))
            else:
                plan.append(("keep", block, None))

        if not units:
            return chapter

        translations = await self._translate_batched(units, chapter_index)

        def resolve(uid: str, orig_text: str) -> tuple[str, str | None]:
            """Map a translated segment back to ``(text, rich)``, validating any inline tags."""
            raw = translations.get(uid)
            src = source_rich[uid]
            if raw is None:
                return orig_text, src  # untranslated: keep source text + styling
            if src is None:
                return raw, None  # plain segment: translated plain text
            san = sanitize_inline(raw)
            if inline_tag_signature(san) == inline_tag_signature(src):
                return strip_tags(san), san  # translated + faithful styling
            # Tags diverged: trust the translated text, drop styling for this one segment.
            self.rich_downgraded += 1
            return strip_tags(san), None

        new_blocks: list[Block] = []
        for kind, block, ref in plan:
            if kind == "text" and isinstance(ref, str):
                text, rich = resolve(ref, self._original_text(block))
                new_blocks.append(block.model_copy(update={"text": text, "rich": rich}))
            elif kind == "list" and isinstance(ref, list) and isinstance(block, ListBlock):
                resolved = [resolve(uid, orig) for uid, orig in zip(ref, block.items, strict=True)]
                items = [t for t, _ in resolved]
                out_riches = [r for _, r in resolved]
                items_rich = out_riches if any(r is not None for r in out_riches) else None
                new_blocks.append(
                    block.model_copy(update={"items": items, "items_rich": items_rich})
                )
            else:
                new_blocks.append(block)

        new_title = chapter.title
        if title_uid is not None and chapter.title is not None:
            new_title, _ = resolve(title_uid, chapter.title)
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
                batch, self.model, self.target_lang, self.source_lang, self.glossary
            )
            if cp is not None:
                cached = self._cached_batch(cp, key, source_hash, batch)
                if cached is not None:
                    out.update(cached)
                    self.reused_batches += 1
                    continue
            translated = await self._translate_batch_resilient(batch)
            out.update(translated)
            self.untranslated_units += len(batch) - len(translated)
            # Only a COMPLETE batch is cacheable. A model can answer with valid JSON that is
            # missing segments; caching that would freeze those segments in the source language
            # forever (no --resume would ever retry them) and silence untranslated_units on the
            # resumed run — a clean-looking run hiding untranslated text.
            if cp is not None and len(translated) == len(batch):
                cp.record(key, {"source_hash": source_hash, "translations": translated})
        return out

    @staticmethod
    def _cached_batch(
        cp: CheckpointManager, key: str, source_hash: str, batch: dict[str, str]
    ) -> dict[str, str] | None:
        """Return the cached translations for ``batch``, or ``None`` to translate it again.

        A record is reused only when it matches the source fingerprint *and* covers every unit
        in the batch, so a truncated or hand-edited checkpoint can never silently drop segments.
        """
        payload = cp.get(key)
        if payload is None or payload.get("source_hash") != source_hash:
            return None
        cached = payload.get("translations")
        if not isinstance(cached, dict):
            return None
        translations = {str(k): v for k, v in cached.items() if isinstance(v, str)}
        if set(translations) != set(batch):
            return None
        return translations

    async def _translate_batch_resilient(self, batch: dict[str, str]) -> dict[str, str]:
        """Translate a batch, bisecting on persistent failure to isolate a poison segment.

        One malformed segment (e.g. inline HTML the model can't emit as valid JSON) fails the
        whole batch. Rather than lose all N units, we split and retry each half, so only the
        offending segment(s) stay untranslated — the neighbours are recovered in the same run.
        """
        for _attempt in range(self.max_retries):
            try:
                return await self._translate_units(batch)  # partial-but-valid replies pass through
            except TranslateError:
                continue  # unparseable reply; retry, then bisect below
        # Persistent JSON failure: split and retry each half so one poison segment doesn't sink
        # its neighbours. Recurses until the offending segment is alone (then dropped to source).
        if len(batch) > 1:
            items = list(batch.items())
            mid = len(items) // 2
            left = await self._translate_batch_resilient(dict(items[:mid]))
            right = await self._translate_batch_resilient(dict(items[mid:]))
            return {**left, **right}
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
                # Keep only the ids we asked for. A model that echoes an id from another batch
                # would otherwise overwrite that unit's real translation with unrelated text.
                if isinstance(value, str) and str(key) in units:
                    result[str(key)] = value
        return result

    @staticmethod
    def _original_text(block: Block) -> str:
        return getattr(block, "text", "")


def _batch_fingerprint(
    batch: dict[str, str],
    model: str,
    target_lang: str,
    source_lang: str | None,
    glossary: Glossary | None,
) -> str:
    """SHA-1 over model, target language, source language, glossary prompt, and batch content.

    This guarantees that switching model or target language, or editing the glossary,
    invalidates cached translations — a stale checkpoint from ``--to Spanish`` is never
    reused for ``--to French``, nor one from another model.
    """
    fp = Fingerprint()
    fp.field(model)
    fp.field(target_lang)
    fp.field(source_lang or "")
    fp.field(glossary.prompt_block() if glossary and glossary.terms else "")
    for uid, text in sorted(batch.items(), key=lambda x: x[0]):
        fp.field(uid)
        fp.field(text)
    return fp.hexdigest()


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
