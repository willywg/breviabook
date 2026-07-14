# PRP: `breviabook translate` — translation without condensation

> Product Requirement Prompt for **BreviaBook**. Goal: enough context + executable
> validation for one-pass implementation. Source of truth: [docs/ROADMAP.md](../docs/ROADMAP.md).
> Operating rules: [CLAUDE.md](../CLAUDE.md).

## Goal

Ship a first-class **`breviabook translate INPUT --to Spanish`** command that translates a book
**without condensing it**, reusing the existing pipeline and `Translator` unchanged. Add a
**translation checkpoint** so an interrupted translate run resumes instead of re-paying for the
whole book.

Post-MVP feature (not a ROADMAP phase). Builds on Phase 10 (`translate/translator.py`).

## Why

- Users want the *whole* book in their language — condensation is a separate decision from
  translation, and today the two are welded together (`--translate-to` only runs **after**
  condense/synthesize).
- The `Translator` is already condensation-agnostic: `translate_document()` takes any `Document`
  and rewrites only prose blocks (`HeadingBlock`, `ParagraphBlock`, `QuoteBlock`, `ListBlock`),
  leaving `code`, `table` and `image` blocks untouched. Nothing in it assumes a condensed input.
- Translate-only is the mode that makes the **most** LLM calls (it walks the full book, not the
  ~30% condensed version), so it is the mode that most needs `--resume` — and it is currently the
  only one without it. The existing checkpoint stores `CondensedChunk`s, which never run here.

### Cost note (correcting a common misreading)

Translate-only is **not** ~3× more expensive than condense+translate. It drops the condense and
synthesize calls entirely. For an input of N tokens at `ratio=0.30`, with
`gemini-3-flash-preview` ($0.50/1M in, $3.00/1M out):

| Mode | Prompt | Completion | Relative cost |
|---|---|---|---|
| condense + translate | ~1.6 N | ~0.9 N | 1.00× |
| translate-only | ~1.0 N | ~1.15 N | **~1.13×** |

The ~1.15 completion factor is target-language expansion (EN→ES runs ~10–20% longer). The two
modes cost roughly the same; they differ in *output*, not in price.

## Scope

**In scope:**
- New CLI command `breviabook translate` (own command — `condense --translate-only` reads as a
  contradiction).
- `condense_book(..., translate_only: bool = False)`: skip chunk → condense → synthesize; feed
  the freshly-parsed `Document` straight to the `Translator`.
- Translation checkpoint + `--resume`, **translate-only mode only** (see "Resume strategy").
- `estimate_condense(..., translate_only=True)`: a translation-shaped `--dry-run` estimate
  (the current formula hardcodes the condensation cost model and would be ~3× wrong here).
- Mode-aware reporting: no "compression %" row when nothing was compressed; surface
  `Translator.untranslated_units` as a warning (useful in **both** modes — it is currently
  counted and silently dropped).
- Share the CLI plumbing between `condense` and `translate` instead of copy-pasting ~80 lines.

**Out of scope:**
- Any change to `Translator`'s prompt, batching or fallback behaviour.
- Any change to the renderers, the IR, or the parsers.
- Translation checkpointing in **condense+translate** mode (see "Resume strategy" for why).
- Per-batch parallelism / concurrency changes.

## Non-negotiable constraints (from CLAUDE.md / ROADMAP §14)

- [ ] Clean-room: no code copied/translated from reference repos.
- [ ] No GPL/AGPL runtime deps. This PRP adds **no** new dependency (`hashlib` is stdlib).
- [ ] `code` and `image` blocks are never summarized or split — the `Translator` already skips
      them; do not touch that logic.
- [ ] No secrets or job state committed. Checkpoints live under the gitignored `.breviabook/`.

## Context & references

```yaml
# Read these before writing code:
- breviabook/pipeline.py                 # condense_book() + estimate_condense() — the branch point
- breviabook/translate/translator.py     # Translator; translate_document/translate_chapter/_translate_batched
- breviabook/persistence/checkpoint.py   # CheckpointManager — generic (chunk_id -> dict), REUSE AS-IS
- breviabook/condense/condenser.py       # how a checkpoint is threaded through a pass (copy the shape)
- breviabook/images/selector.py          # WHY it must still run in translate-only (dangling ImageBlocks)
- breviabook/cli.py                      # condense command — the plumbing to factor out
- breviabook/ui/progress.py              # ProgressReporter Protocol (phase/advance/note)
- tests/test_translator.py               # mock-provider patterns for translation
- tests/test_pipeline.py                 # end-to-end pipeline tests with the mock provider
```

### Three findings that must shape the implementation

1. **Do NOT skip `ImageSelector`.** It is tempting ("nothing was cut, every image survives"), but
   `select()` also **strips dangling `ImageBlock`s** — references to assets the parser never
   extracted (`images/selector.py:41`). Skipping it lets the renderers emit broken links in the
   EPUB. It is LLM-free, cheap, and idempotent: when nothing was cut, `kept` == all images. Leave
   it in the flow for both modes.
2. **`--rank-images` still applies** in translate-only (dropping decorative images is orthogonal
   to condensing text). Keep the phase where it is; it works unchanged.
3. **`--target-ratio` and `--chunk-tokens` are meaningless here.** The `translate` command simply
   does not expose them — no flag to ignore, no warning to print.

## Resume strategy

### Design

Reuse `CheckpointManager` **unchanged**. Its record is generic — an id keyed to a `dict` payload —
so translation only needs its own key namespace and payload:

```
key:     f"tr:{chapter_index}:{batch_start}"        # e.g. "tr:7:40"
payload: {"source_hash": "<sha1>", "translations": {"41": "…", "42": "…"}}
```

- **Granularity = one translation batch** (`max_units_per_batch=40`), which is exactly one LLM
  call. That matches the condenser's "one checkpoint record per LLM call" rule: an interrupted run
  loses at most the in-flight call.
- **`source_hash` is the safety interlock.** It is a SHA-1 over the batch's joined source segments.
  On resume, a record is reused **only if** the hash of the batch about to be translated matches
  the stored one. If the user edited the book, changed the target language, or points at a
  different file that happens to share a stem, the hashes differ and the batch is re-translated.
  Without this guard a stale checkpoint would silently splice the *old* book's translation into
  the new one — a correctness bug, not just wasted work.
- Include the **target language and glossary fingerprint** in the hashed material, so switching
  `--to Spanish` → `--to French` (or changing the glossary) invalidates the records rather than
  reusing Spanish text.

### Why translate-only, and not condense+translate

In condense+translate, the `Translator`'s input is the **synthesized** document — LLM output.
Synthesis is not checkpointed, so it re-runs on every resume and produces different text. The
batch keys would then point at records whose `source_hash` no longer matches, so every batch would
be re-translated anyway: all of the complexity, none of the savings. In translate-only the input is
the **parsed** document, which is deterministic — the same file always yields the same units, so
the cache always hits.

Therefore: `condense_book` passes a checkpoint to the `Translator` **only when
`translate_only=True`**. Condense+translate keeps today's behaviour exactly (its own chunk
checkpoint still works).

### Checkpoint file

`out_dir/.breviabook/{stem}.jsonl`, where `stem = f"{input_path.stem}-{lang_slug}"` (e.g.
`ai-engineering-spanish`). Same gitignored directory as the condense checkpoint. Keys are
namespaced (`tr:`), so even a shared file could never collide with condense records.

## Implementation blueprint

1. **`breviabook/translate/translator.py`**
   - `translate_document(doc, *, checkpoint: CheckpointManager | None = None, on_progress=None)` —
     pass the chapter index down.
   - `translate_chapter(chapter, chapter_index: int = 0, *, checkpoint=None)`.
   - `_translate_batched(units, chapter_index, checkpoint)` — for each batch compute
     `key`/`source_hash`; on a hit with a matching hash, reuse the stored translations and make **no**
     LLM call; otherwise translate, then `checkpoint.record(key, payload)`. Count reused batches on
     the instance (`self.reused_batches`) for reporting.
   - `_batch_fingerprint(batch)` — SHA-1 over `target_lang`, `source_lang`, the glossary's prompt
     block, and the batch's `uid + text` pairs. Keep it a small private helper; `hashlib` is stdlib.
   - `count_translatable_units(doc) -> int` — module-level helper (walk chapters, count title +
     prose blocks + list items). Needed by the dry-run estimate; keep the counting rule in **one**
     place so the estimate cannot drift from the translator.
   - Keep the existing retry/fallback path untouched: a batch that fails after retries still falls
     back to source text, and **must not** be checkpointed (never cache a failure).

2. **`breviabook/pipeline.py`**
   - `condense_book(..., translate_only: bool = False)`.
   - When `translate_only`: require `translate_to` (raise `ValueError` otherwise); skip `Chunker`,
     `Condenser`, `Synthesizer` and their checkpoint; set `working_doc = doc`; report a single
     `Translate` phase totalling `len(doc.chapters)`.
   - Build the translation `CheckpointManager` (cleared unless `--resume`) and pass it to
     `translate_document`.
   - **Still run** `ImageSelector` and (if requested) `VisionRanker` in both modes.
   - After translation, in **both** modes: if `translator.untranslated_units` > 0, append a warning
     (`"N segments left untranslated (model response unparseable after retries)"`).
   - Add `CondenseResult.translate_only: bool` and `.batches_reused: int` so the CLI can report
     honestly without re-deriving the mode.
   - `estimate_condense(..., translate_only: bool = False)`: when set, use the translation cost
     model instead of the condensation one —
     `units = count_translatable_units(doc)`, `batches = ceil(units / 40)`,
     `prompt ≈ input_tokens + PROMPT_OVERHEAD_PER_BATCH * batches`,
     `completion ≈ input_tokens * TRANSLATION_EXPANSION` (module constant, `1.15`, with a comment
     explaining it is target-language expansion), `estimated_output_tokens = completion`.
     Add `Estimate.translatable_units` / `Estimate.batches`.

3. **`breviabook/cli.py`**
   - Factor the shared plumbing out of `condense` into helpers (settings, `validate_formats`,
     `--manual-toc` load, `--glossary` load, `get_provider`, banner, reporter selection, the Done
     and LLM-usage tables). Aim: `translate` adds a command, not a second copy of the file.
   - New `@app.command() translate`:
     `INPUT`, `--to` (required), `--from`, `--glossary`, `--formats`, `--out`, `--resume`,
     `--provider`, `--model`, `--api-endpoint`, `--reasoning-effort`, `--rank-images`,
     `--manual-toc`, `--dry-run`. No `--target-ratio`, no chunk options.
   - Calls `condense_book(translate_only=True, translate_to=..., ...)`.
   - Result table: when `translate_only`, print `size change: +15%` instead of
     `compression: -15% smaller`, and drop the "chunks reused" row in favour of
     `batches reused: N` when resuming.

4. **Docs** — README: a `translate` section under Quickstart + the CLI block; a line in CHANGELOG
   under Unreleased.

### New / changed files

- `breviabook/translate/translator.py` — checkpoint support, `count_translatable_units`.
- `breviabook/pipeline.py` — `translate_only` branch, translation checkpoint, untranslated warning,
  translation-shaped dry-run estimate.
- `breviabook/cli.py` — new `translate` command; shared plumbing extracted.
- `tests/test_translator.py` — checkpoint reuse, hash-mismatch invalidation, failures not cached.
- `tests/test_pipeline.py` — translate-only path.
- `tests/test_cli.py` — the `translate` command.
- `README.md`, `CHANGELOG.md`.

## Validation gates (must all pass)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict breviabook
uv run pytest -q
uv run pip-licenses --fail-on "GPL"
```

### Feature-specific checks

- [ ] Translate-only with the mock provider makes **zero** condense/synthesize calls (assert the
      provider's call count equals the number of translation batches).
- [ ] `code`, `table` and `image` blocks come out byte-identical to the parsed input; every prose
      block is translated.
- [ ] A book with a dangling `ImageBlock` (reference to a missing asset) still renders without a
      broken link — i.e. `ImageSelector` ran.
- [ ] Resume: a run interrupted after k batches, re-run with `--resume`, makes exactly
      `total − k` LLM calls and produces byte-identical output to an uninterrupted run.
- [ ] A checkpoint written for target language A is **not** reused when translating to language B
      (hash guard); same for an edited source file.
- [ ] A batch that fails after retries is not written to the checkpoint (the next `--resume`
      retries it).
- [ ] `--dry-run --to Spanish` reports completion ≈ `input_tokens × 1.15` and does **not** apply
      `target_ratio`.
- [ ] All tests use the deterministic mock provider — no real LLM call.

## Acceptance criteria

- [ ] `breviabook translate book.epub --to Spanish --formats epub` produces a full-length
      translated EPUB with code/tables/images intact.
- [ ] Killing that run mid-way and re-running with `--resume` re-uses the completed batches and
      only pays for the rest.
- [ ] `breviabook condense` behaves exactly as before (no regression in the condense+translate path).
- [ ] `breviabook translate --dry-run` gives a cost within ~20% of the real run.
- [ ] The result table never claims "compression" for a run that compressed nothing.
- [ ] All validation gates green.

## Confidence score

8/10 — The pipeline branch and the CLI command are mechanical. The risk sits in the checkpoint
hash guard (key/fingerprint must cover language + glossary or a resume can splice stale text) and
in the CLI refactor, which touches the one file with no strong test coverage today.
