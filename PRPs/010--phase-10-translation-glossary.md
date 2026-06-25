# PRP: Phase 10 — Integrated translation + glossary

> Product Requirement Prompt for **BreviaBook**. Source of truth: [docs/ROADMAP.md](../docs/ROADMAP.md) §6, §7.5, §10 (Phase 10).
> Operating rules: [CLAUDE.md](../CLAUDE.md). Builds on PRP 005 (synthesis) and PRP 009 (providers).

## Goal

Translate the **already-condensed** IR to a target language in the same pass, using the same
LLM layer plus an optional glossary for term consistency. Code/tables/images are never
translated; identifiers/URLs/paths inside prose are preserved.

## Why

- The owner wants the result in Spanish even when the source is English (§1).
- Translating the condensed book (not the full book) is much cheaper (§7.5).

## Scope

**In scope:**
- `translate/glossary.py`: `Glossary` (term map from JSON) + prompt block.
- `translate/translator.py`: `Translator(provider, model, target_lang, source_lang, glossary)`
  with `translate_document` / `translate_chapter`. Translatable units = chapter title +
  heading/paragraph/quote text + list items; code/table/image preserved. One LLM call per
  chapter (JSON id→translation), missing translations fall back to the original.
- Pipeline: run translation after synthesis, before image-select/render (replace the old
  Phase-10 warning). CLI: wire `--translate-to`, `--source-lang`, `--glossary`.
- Tests (scripted provider; no network).

**Out of scope:** NER auto-glossary (spacy, later), translating `metadata.title` (kept as-is
for v1), separate translation checkpoint (condense is already checkpointed).

## Non-negotiable constraints (CLAUDE.md / ROADMAP §7.5)

- [ ] Code blocks and tables are never translated; image assets untouched.
- [ ] Prose preserves identifiers, API names, file paths, URLs, numbers.
- [ ] Glossary terms applied consistently when provided.
- [ ] Translation reuses the existing provider (usage/cost accrues automatically).
- [ ] A chapter with no translatable text makes no LLM call.

## Context & references

```yaml
- docs/ROADMAP.md          # §7.5 integrated translation, §2.1 glossary idea
- breviabook/ir/models.py      # block types; model_copy for rebuilds
- breviabook/llm/base.py       # LLMProvider
- breviabook/utils/jsonx.py    # extract_json_object
- breviabook/pipeline.py       # insert translation after synthesis
# Study (never copy): TBL glossary + preserve_technical_content prompt idea
```

## Design

- `Glossary.from_json(path)`: `{source_term: target_term}` → `prompt_block()` lists them.
- `translate_chapter`: collect numbered units (title + text blocks + each list item), send with
  the glossary, parse `{"translations": {id: text}}`, rebuild blocks via `model_copy` (types &
  levels preserved), fall back to originals for any missing id.
- `translate_document`: map over chapters; keep `images` and `metadata` unchanged.

## Implementation blueprint

1. `translate/glossary.py`.
2. `translate/translator.py` (+ prompt builder, `TranslateError`).
3. `pipeline.py`: `condense_book(..., source_lang, glossary)` → translate after synthesis.
4. `cli.py`: load `--glossary`, pass `--translate-to`/`--source-lang`/glossary.
5. Tests: `tests/test_glossary.py`, `tests/test_translator.py`, pipeline translate e2e.

### New / changed files

- `breviabook/translate/glossary.py`, `breviabook/translate/translator.py`
- `breviabook/pipeline.py`, `breviabook/cli.py`
- tests as above

## Validation gates (must all pass)

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict breviabook
uv run pytest -q && uv run pip-licenses --fail-on "GPL"
```

### Feature-specific checks

- [ ] Headings/paragraphs/quotes/list-items + chapter title are translated; code/table/image
      unchanged; list/heading types & levels preserved.
- [ ] Code text never appears in the units sent to the LLM.
- [ ] A missing translation id falls back to the original text.
- [ ] Glossary terms appear in the prompt; empty glossary adds nothing.
- [ ] Pipeline with `--translate-to` produces translated output and no longer warns; no LLM
      call for a code-only chapter.

## Acceptance criteria

- [ ] `Translator(mock).translate_document(doc)` returns a translated IR with structure intact.
- [ ] `breviabook condense book.epub --translate-to Spanish [--glossary g.json]` works end-to-end.
- [ ] All five validation gates green.

## Confidence score

8/10 — Mirrors the condenser's segment/serialize/reassemble pattern; main care is robust
id-based reassembly and keeping code out of the translation payload.
