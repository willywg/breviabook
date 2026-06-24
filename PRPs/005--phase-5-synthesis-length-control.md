# PRP: Phase 5 — Hierarchical synthesis + length control

> Product Requirement Prompt for **Brevia**. Source of truth: [docs/ROADMAP.md](../docs/ROADMAP.md) §5, §6, §7.3, §10 (Phase 5).
> Operating rules: [CLAUDE.md](../CLAUDE.md). Builds on PRP 004 (condenser).

## Goal

Add level 2 of the hierarchical summarization (§7.3): a **per-chapter** synthesis pass that
smooths transitions across chunk boundaries and trims the chapter toward the
`--target-ratio`, with an active length-control loop (extra trimming passes when over
budget). Code/tables/images stay intact. Tests use the deterministic mock provider.

## Why

- Per-chunk condensation (Phase 4) is choppy and only *asks* the model for a ratio; real
  length tuning happens here (§7.3: "This is where the real length is tuned").
- Produces the smoothed, length-controlled chapters the renderers consume.

## Scope

**In scope:**
- `brevia/condense/common.py`: extract shared primitives so condenser + synthesizer don't
  duplicate them — `Segment`, `segment_blocks`, `run_text`, `structural_marker`,
  `split_paragraphs`, `extract_json`, `CondenseError`.
- Refactor `condense/condenser.py` to use `common.py` (no behavior change).
- `brevia/condense/synthesizer.py`: `SynthesizedChapter` model + `Synthesizer` with
  per-chapter synthesis, token budget from original chapter size, and a bounded trim loop.
- Add `SYNTH_SYSTEM_PROMPT` + `build_synthesize_messages` to `condense/prompts.py`.
- Tests.

**Out of scope:** translation (Phase 10), renderers beyond MD (Phases 6–7), CLI wiring.

## Non-negotiable constraints (CLAUDE.md / ROADMAP §7.2–7.3)

- [ ] Code/tables/images preserved verbatim (only prose runs go to the LLM).
- [ ] Image keep/drop is already decided in Phase 4 — synthesis must NOT re-drop kept images.
- [ ] Length control is bounded (max N trim passes) — never an unbounded loop.
- [ ] Block order/interleaving preserved.
- [ ] Existing Phase 4 tests keep passing after the refactor.

## Context & references

```yaml
- docs/ROADMAP.md          # §7.3 two-level hierarchy + length control, §5 step 4
- brevia/condense/condenser.py  # CondensedChunk (input_tokens, blocks, kept_image_ids)
- brevia/condense/chunker.py    # for token helpers
- brevia/utils/tokens.py        # block_tokens
```

## Design

- Group `list[CondensedChunk]` by `chapter_index` (stable order).
- Per chapter: `input_tokens = Σ chunk.input_tokens` (ORIGINAL size);
  `target_tokens = round(target_ratio * input_tokens)`.
- Segment the chapter's condensed blocks; serialize prose runs as `[TEXT n]` with structural
  markers (`[CODE]`, `[IMAGE]`, `[TABLE]`, `[HEADING]`) for context; ask the model to smooth
  + trim to ~target words; return `{"texts": {...}}`.
- Reassemble: `[TEXT n]` → `ParagraphBlock`(s); structural/image blocks preserved in place.
- Length control: if `output_tokens > target_tokens * (1 + tolerance)`, run up to
  `max_trim_passes` extra "condense further" passes. Stop when under budget, when no prose
  remains, or at the pass cap.
- Chapters with no prose runs pass through unchanged (no LLM call).

## Implementation blueprint

1. `condense/common.py` — shared primitives (moved out of condenser).
2. Refactor `condenser.py` to import them; keep `CondenseError` importable from condenser.
3. `prompts.py` — `SYNTH_SYSTEM_PROMPT`, `build_synthesize_messages(body, target_tokens, smooth)`.
4. `synthesizer.py` — `SynthesizedChapter`, `Synthesizer.synthesize(...)`,
   `synthesize_chapter(...)`, `_synth_pass(...)`; helper to build a `Document` from chapters.
5. Tests: `tests/test_synthesizer.py` + confirm `tests/test_condenser.py` still green.

### New / changed files

- `brevia/condense/common.py` (new), `brevia/condense/synthesizer.py` (new)
- `brevia/condense/condenser.py` (refactor), `brevia/condense/prompts.py` (add synth prompt)
- `tests/test_synthesizer.py` (new)

## Validation gates (must all pass)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict brevia
uv run pytest -q
uv run pip-licenses --fail-on "GPL"
```

### Feature-specific checks

- [ ] Multiple condensed chunks of one chapter become one smoothed chapter; code/images intact.
- [ ] When the first synthesis is over budget, a trim pass runs and reduces tokens; passes are
      bounded by `max_trim_passes`.
- [ ] A chapter under budget triggers no trim pass.
- [ ] A code/image-only chapter passes through with no provider call.
- [ ] `target_tokens` derives from the ORIGINAL chapter size, not the condensed size.
- [ ] Phase 4 tests still pass after the refactor.

## Acceptance criteria

- [ ] `Synthesizer(mock).synthesize(condensed)` returns per-chapter results approaching the
      target ratio, with structure preserved.
- [ ] Length control is active and bounded.
- [ ] All five validation gates green.

## Confidence score

7/10 — Length control with an LLM is inherently approximate; the bounded loop + token
budgeting keep it safe and deterministic under the mock.
