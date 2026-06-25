# PRP: Phase 4 — Condenser

> Product Requirement Prompt for **BreviaBook**. Source of truth: [docs/ROADMAP.md](../docs/ROADMAP.md) §5, §6, §7.1, §7.3, §10 (Phase 4), §12.
> Operating rules: [CLAUDE.md](../CLAUDE.md). Builds on PRP 003 (chunker + checkpoint) and PRP 000 (LLM layer).

## Goal

Condense each chunk with the LLM: produce a condensed set of IR blocks per chunk, mark
images keep/drop, preserve code verbatim, and flag the "output longer than input" error
signal. Wire it through the checkpoint so condensation is resumable. Tests run on the
deterministic `MockProvider` — no real LLM required.

## Why

- First real LLM step; defines the `result` shape the checkpoint stores.
- Per-chunk condensation is level 1 of the hierarchical summarization (§7.3); Phase 5
  synthesis builds on it.

## Scope

**In scope:**
- `breviabook/condense/prompts.py`: condense system prompt + JSON-contract user prompt builder.
- `breviabook/condense/condenser.py`:
  - `CondensedChunk` (pydantic) result model.
  - `Condenser(provider, model, target_ratio)` with `condense_chunk` and a checkpoint-aware
    `condense(chunks, checkpoint=...)`.
  - Segment the chunk so **code/tables/headings are preserved structurally** and only prose
    runs (paragraph/quote/list) are sent to the LLM; images presented as `[IMG:id — "cap"]`
    markers for the keep/drop decision (§7.1).
  - Tolerant JSON extraction; `output_longer_than_input` flag (§7.3).
  - `assemble_condensed_document(original, condensed)` → a condensed `Document` (kept images
    only) so we can render end-to-end.
- Tests with a scripted mock provider.

**Out of scope:** hierarchical synthesis / length-trimming pass (Phase 5), translation
(Phase 10), vision image ranking (Phase 11), full CLI pipeline wiring.

## Non-negotiable constraints (CLAUDE.md / ROADMAP §7.1–7.3, §12)

- [ ] `CodeBlock` (and `TableBlock`) content is preserved **verbatim** — never sent for
      rewriting; restored from the original block, not the model echo.
- [ ] Images are kept/dropped by id; dropped images are excluded from the condensed document.
- [ ] Order/interleaving of blocks is preserved (condense per prose run between structural blocks).
- [ ] "Output longer than input" is detected and flagged, never silently accepted.
- [ ] Condensation is resumable via the checkpoint; a recorded chunk is never re-called.

## Context & references

```yaml
- docs/ROADMAP.md          # §7.1 image markers + keep/drop, §7.3 hierarchy + length, §5 step 3
- breviabook/condense/chunker.py   # Chunk (id, chapter_index, blocks, token_count, prev_context)
- breviabook/llm/base.py       # LLMProvider Protocol, Message
- breviabook/persistence/checkpoint.py  # CheckpointManager (result = dict[str, object])
- tests/conftest.py        # MockProvider (deterministic)
```

## Design — LLM contract

Serialize the chunk into labeled segments: `[TEXT n]` prose runs, `[IMG:id — "cap"]`
markers, code as a "preserved, do not reproduce" fenced block, headings/tables as kept
markers. Ask the model to return ONLY:

```json
{"texts": {"1": "<condensed [TEXT 1]>", "2": "..."}, "essential_images": ["id", ...]}
```

Reassemble in original order: each `[TEXT n]` → condensed `ParagraphBlock`(s) (split on blank
lines); kept blocks (heading/code/table) restored verbatim; image kept iff its id is in
`essential_images`. If a chunk has no prose runs, skip the LLM (passthrough, keep all images).

## Implementation blueprint

1. `prompts.py`: `CONDENSE_SYSTEM_PROMPT`, `build_condense_messages(body, target_ratio, image_ids)`.
2. `condenser.py`: `_segment_chunk`, `_serialize`, `_extract_json`, `CondenseError`,
   `CondensedChunk`, `Condenser`, `assemble_condensed_document`.
3. Tests: `tests/test_condenser.py`.

### New / changed files

- `breviabook/condense/prompts.py`, `breviabook/condense/condenser.py`
- `tests/test_condenser.py`

## Validation gates (must all pass)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict breviabook
uv run pytest -q
uv run pip-licenses --fail-on "GPL"
```

### Feature-specific checks

- [ ] Condensing a chunk replaces prose with the model's condensed text while code blocks
      come through byte-for-byte and block order is preserved.
- [ ] Image with id in `essential_images` is kept; others are dropped and listed in
      `dropped_image_ids`.
- [ ] `output_longer_than_input` is True when the model returns longer text.
- [ ] A code-only chunk is passed through with no provider call.
- [ ] JSON wrapped in ```json fences / surrounded by prose still parses; truly invalid JSON
      raises `CondenseError`.
- [ ] `condense(..., checkpoint=cp)` run twice does not re-call the provider for done chunks.
- [ ] `assemble_condensed_document` groups chunks by chapter and keeps only kept images.

## Acceptance criteria

- [ ] `Condenser(mock).condense_chunk(chunk)` returns a `CondensedChunk` with code intact and
      images resolved.
- [ ] Resumable via checkpoint; "output > input" surfaced.
- [ ] All five validation gates green.

## Confidence score

7/10 — Risk is LLM-output parsing robustness; mitigated by restoring code/images from the
original IR (never trusting the echo) and tolerant JSON extraction.
