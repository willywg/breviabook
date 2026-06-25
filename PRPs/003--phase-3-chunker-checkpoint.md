# PRP: Phase 3 — Chunker + checkpoint

> Product Requirement Prompt for **BreviaBook**. Source of truth: [docs/ROADMAP.md](../docs/ROADMAP.md) §5, §6, §7.2, §9, §10 (Phase 3), §12.
> Operating rules: [CLAUDE.md](../CLAUDE.md). Builds on PRP 001 (IR).

## Goal

Chapter-aware, token-based chunking that **never splits a code block or table**, plus a
checkpoint/resume layer so long jobs survive interruption. No LLM. This produces the unit of
work the Phase 4 condenser consumes, and the persistence the `--resume` flag relies on.

## Why

- The condenser/synthesizer/translator (Phases 4–5, 10) all operate per chunk; the chunk is
  the pipeline's currency.
- `--resume` (acceptance criterion §13.4) needs durable per-chunk results.

## Scope

**In scope:**
- `breviabook/utils/tokens.py`: `count_tokens(text)` via `tiktoken` with a char-based fallback;
  `block_text(block)` / `block_tokens(block)` helpers.
- `breviabook/condense/chunker.py`: `Chunk` model + `Chunker(max_tokens).chunk(doc) -> list[Chunk]`.
  Chapter-aware, ~2000-token groups, atomic blocks (code/tables never split), light
  previous-block context for continuity.
- `breviabook/persistence/checkpoint.py`: `CheckpointManager` — durable, append-only per-chunk
  results (JSONL), resumable, last-write-wins.
- Tests for all three.

**Out of scope:** the condense prompt/LLM call (Phase 4), synthesis (Phase 5), wiring into
the CLI pipeline.

## Non-negotiable constraints (CLAUDE.md / ROADMAP §7.2, §12)

- [ ] A `CodeBlock` or `TableBlock` is NEVER split across chunks (blocks are atomic units).
- [ ] Chunks never cross chapter boundaries.
- [ ] Chunk size targets ~2000 tokens (`DEFAULT_CHUNK_TOKENS`), **not** ~450.
- [ ] Checkpoint files are job state — already gitignored (`checkpoints/`, `.breviabook/`, `*.sqlite`).
- [ ] Resuming must not reprocess a chunk already recorded.

## Context & references

```yaml
- docs/ROADMAP.md          # §7.2 chunking rules, §5 checkpoint between [2]-[5], §9 defaults
- breviabook/ir/models.py      # Document/Chapter/Block union
- breviabook/config.py         # default_chunk_tokens
# Reference (study, never copy): cognitivetech chunk-size lesson; OllamaBook CHUNK_OVERLAP idea
```

## Design

- `block_text(block)`: textual content per block kind (code->text, table->joined cells,
  list->joined items, image->caption, heading/paragraph/quote->text).
- `count_tokens`: cache a `cl100k_base` encoding; if unavailable, estimate `len(text)//4`.
  (It's an estimate for budgeting, independent of the actual model's tokenizer.)
- `Chunk(id, chapter_index, chapter_title, blocks, token_count, prev_context)`.
  `id` = `ch{chapter_index}-{n}` (stable across runs → resume keys line up).
  `prev_context` = trailing text of the previous chunk in the same chapter (continuity), kept
  SEPARATE from `blocks` so it is never re-condensed/duplicated.
- `Chunker.chunk`: per chapter, greedily accumulate blocks until the next would exceed
  `max_tokens`, then flush. A single block larger than `max_tokens` becomes its own chunk
  (still never split). Reset numbering per chapter.

- `CheckpointManager(path)`:
  - JSONL store, one record `{"chunk_id", "result"}` per line; append + flush on write so a
    crash mid-job loses at most the in-flight chunk.
  - `is_done(id)`, `get(id)`, `record(id, result)`, `results()`, `clear()`.
  - Loads existing file on init (last record wins per id).

## Implementation blueprint

1. `utils/tokens.py` — counting + block text helpers.
2. `condense/chunker.py` — `Chunk` (pydantic) + `Chunker`.
3. `persistence/checkpoint.py` — `CheckpointManager`.
4. Tests: `tests/test_tokens.py`, `tests/test_chunker.py`, `tests/test_checkpoint.py`.

### New / changed files

- `breviabook/utils/tokens.py`, `breviabook/condense/chunker.py`, `breviabook/persistence/checkpoint.py`
- `tests/test_tokens.py`, `tests/test_chunker.py`, `tests/test_checkpoint.py`

## Validation gates (must all pass)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict breviabook
uv run pytest -q
uv run pip-licenses --fail-on "GPL"
```

### Feature-specific checks

- [ ] No chunk contains blocks from more than one chapter.
- [ ] A code block placed between text never lands split; an oversized code block becomes its
      own chunk intact.
- [ ] Most chunks are within ~max_tokens; `token_count` matches `count_tokens` of their text.
- [ ] `prev_context` is populated for 2nd+ chunks in a chapter and absent for the first.
- [ ] CheckpointManager: record → new manager on same path sees `is_done` true and same result;
      `clear()` resets; corrupt/blank lines are skipped without crashing.

## Acceptance criteria

- [ ] `Chunker().chunk(parse(sample.epub))` yields chapter-bounded, code-safe chunks.
- [ ] A simulated resume skips already-recorded chunks.
- [ ] All five validation gates green.

## Confidence score

8/10 — Straightforward; main care points are token-count determinism and atomic JSONL writes.
