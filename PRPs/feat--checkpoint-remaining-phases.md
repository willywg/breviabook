# PRP: Checkpoint the remaining paid phases (synthesis, translate-after-condense, vision)

> Product Requirement Prompt for **BreviaBook**. Follow-up to
> `feat--condense-checkpoint-fingerprint` (which named this work as out of scope).
> Goal: no paid LLM work is ever repaid on `--resume`.
> Operating rules: [CLAUDE.md](../CLAUDE.md).

## Goal

Three LLM phases still re-run from scratch on a resumed job:

1. **translate-after-condense** — `pipeline.py` builds the post-synthesis `Translator`
   **without** `checkpoint=`, so a condense+translate run that dies mid-translation
   repays the entire translation (only `translate_only` mode wires a checkpoint today).
2. **synthesis** — one LLM smooth pass (+ up to N trim passes) per chapter, not cached.
3. **vision ranking** (`--rank-images`) — one vision call per kept image, not cached.

Give each phase a fingerprinted checkpoint reusing the shared
`breviabook.persistence.fingerprint.Fingerprint` helper, under the same two rules as the
condenser: **only successful results are cached**, and **reuse requires fingerprint match
+ payload validation**.

## Why

- Paid runs still leak money: a crash at 90% of translation re-bills 100% of translation.
- The helper was extracted precisely to be shared; leaving these phases uncached keeps the
  asymmetry the previous PRP started removing.
- Every fingerprint field below was **audited against the real prompt builders**, not
  recalled from memory (see "Fingerprint audit" per phase).

## The namespacing decision (explicit, required)

Today: condense writes keys `ch{i}-{n}` to `{stem}-condensed.jsonl`; translate-only writes
`tr:{chapter}:{start}` to `{stem}-{lang}.jsonl`. If synthesis (`0`, `1`, …) and vision
(`fig1`, …) wrote bare ids, non-collision would be luck, not design.

**Decision: one checkpoint file per pipeline run, keys namespaced by phase prefix.**

- The condense run keeps `{stem}-condensed.jsonl`; it gains `syn:{chapter_index}`,
  `tr:{chapter_index}:{start}`, and `img:{image_id}` records alongside the existing bare
  `ch{i}-{n}` condense keys.
- Collision-freedom becomes **constructed, not accidental**: condense ids match
  `^ch\d+-\d+$` and never contain `:`; every other phase's key carries a `prefix:`
  ending in a colon. A test asserts this invariant over a fully populated file.
- Rejected alternative — one file per phase (`-synthesis.jsonl`, `-vision.jsonl`, …):
  N managers to wire, N `clear()` sites on a fresh run (forgetting one is a stale-reuse
  bug the fingerprint would have to catch), for zero isolation benefit — records are
  already content-validated. One run = one file = one atomic `clear()`.
- Filename stays `{stem}-condensed.jsonl` despite now holding other phases: it names the
  *run* (condense pipeline) vs. the translate-only run's `{stem}-{lang}.jsonl`. Renaming
  orphans existing files for no functional gain (checkpoints are disposable job state).
- Known edge, accepted: `--to condensed` in translate-only mode produces the same
  filename as a condense run's checkpoint. Cross-contamination requires identical target
  language *and* identical content — the fingerprint includes both — so the worst case is
  a recompute. Documented in `checkpoint.py`'s docstring.

## Fingerprint audit (fields verified against the prompt builders)

**Synthesis** — `build_synthesize_messages(body, target_tokens, smooth)`
(`condense/prompts.py`): the prompt is a function of the serialized blocks, the target
length (`max(1, round(target_tokens / 1.3))` words), and the smooth/trim action.
`target_tokens = max(round(target_ratio * sum(cc.input_tokens)), min_target_tokens)`;
trim-loop behavior depends on `tolerance` and `max_trim_passes`. Output also passthroughs
`chapter_index`, `chapter_title`, `kept_image_ids`.
→ fields: `model`, `repr(target_ratio)`, JSON of `[tolerance, max_trim_passes,
min_target_tokens]` (constructor params living on `self` — cheap to capture, no
`getattr`), then per condensed chunk **in order**: `repr(cc.input_tokens)` (NOT derivable
from the condensed blocks — it is the *original* chunk's size; blocks alone would
under-invalidate), `cc.chapter_title or ""`, canonical JSON of `cc.kept_image_ids`,
canonical JSON of the chunk's block dump (order-preserving; `sort_keys` within blocks).

**Vision** — `build_vision_prompt(context)` + `(asset.data, asset.mime)` + `model`
(`images/vision_ranker.py`): the prompt is a function of `context` (surrounding text +
current caption, ≤600 chars) and the image bytes; the decision applies `threshold`;
`update_captions` decides whether the caption output changes.
→ fields: `model`, `repr(threshold)`, `repr(update_captions)`, the computed `context`
string, `asset.mime`, `sha256(asset.data).hexdigest()` (image content — same image id
with new bytes must re-rank).

**Translate-after-condense** — audited `build_translate_messages` against
`_batch_fingerprint` and found a **pre-existing gap**: the translator fingerprint covers
target lang + source lang + glossary + batch content, but **not `model`**. A
`translate --resume` with a different `--model` silently reuses stale translations today,
and wiring the translator here as-is would inherit the gap.
→ this PRP adds `model` as the first field of `_batch_fingerprint` (the value lives on
`self.model` — no `getattr`). **Deliberate digest change**: existing translate checkpoints
invalidate once and recompute. Acceptable per the established rule that checkpoints are
disposable job state; called out here because the previous PRP held translator digests
byte-identical and this one intentionally does not.

Transitive invalidation is by construction: change condense config → condensed content
changes → synthesis and translation fingerprints change → those phases recompute too.
No config needs to propagate downstream.

## Scope

**In scope:**
- `persistence/checkpoint.py`: docstring gains the namespace contract (prefix per phase,
  condense ids never contain `:`) and the two new payload shapes
  (`{"source_hash", "chapter"}` / `{"source_hash", "verdict"}`), plus the
  `--to condensed` edge note.
- `translate/translator.py`: `model` joins `_batch_fingerprint` (first field); the
  `Translator` call in the condense path passes `checkpoint=`.
- `condense/synthesizer.py`:
  - `_chapter_fingerprint(chunks, model, target_ratio, tolerance, max_trim_passes,
    min_target_tokens)` per the audit above.
  - `synthesize(..., checkpoint=None)`; key `syn:{chapter_index}`; payload
    `{"source_hash", "chapter": sc.model_dump(mode="json")}`; reuse requires hash match +
    `SynthesizedChapter.model_validate` (ValidationError → recompute).
  - `SynthesizedChapter.synthesis_failed: bool = False` — set when the smooth pass
    returns `None` after retries (today's silent degraded fallback). Failed chapters are
    **not cached** (a resume retries them); trim-pass degradation after a successful
    smooth *is* cached (the expensive work is done) — stated in the docstring.
  - `Synthesizer.reused_chapters` counter.
- `images/vision_ranker.py`:
  - `_image_fingerprint(...)` per the audit above.
  - `_Verdict` promoted to a pydantic model `Verdict` with `parsed: bool` (excluded from
    dumps); parse failure sets `parsed=False` instead of masquerading as a genuine keep.
  - `rank(doc, checkpoint=None)`; key `img:{image_id}`; payload
    `{"source_hash", "verdict": {...}}`; reuse requires hash match +
    `Verdict.model_validate`; `parsed=False` verdicts are not cached.
  - `VisionRanker.reused_images` counter.
- `pipeline.py`:
  - Translate-after-condense `Translator(...)` gets `checkpoint=checkpoint`;
    `batches_reused` is collected in the condense path too.
  - Synthesis and vision calls pass the run checkpoint; vision runs outside the
    condense/translate-only branch, so both branches expose a common `run_checkpoint`.
  - Warnings: `chapter {i}: synthesis failed after retries; kept condensed text`
    (mirrors the condense-failed warning).
  - `CondenseResult` gains `chapters_reused` / `images_reused`; CLI prints them like the
    existing `chunks reused` / `batches reused` rows.
- Tests.

**Out of scope:**
- TOC inference (`infer_toc`, PDF-only, one pre-chunking call): named, deferred — it runs
  before the checkpoint file's stem is even meaningful, and its cost is one call.
- Prompt-version field and `reasoning_effort`: same parity decision as the previous PRP —
  absent in all four phases by design; if revisited, revisit all together.
- Provider exceptions mid-phase: they propagate today and still will (only parse-level
  degradation is retried/cache-gated).
- Old records written by previous versions: treated as stale, recomputed once (checkpoints
  are disposable). This includes translate checkpoints invalidated by the new `model`
  field.
- `ImageSelector`, parsing, rendering: no LLM, nothing to checkpoint.

## Non-negotiable constraints (CLAUDE.md)

- [ ] No new runtime deps (`hashlib`, `json` stdlib; `pydantic` already a dep).
- [ ] Same-config `--resume` makes **zero** LLM calls across all four phases (proved by a
      call-counting, phase-routing mock provider).
- [ ] Changing model / ratio / chunking / glossary / target lang / image bytes → zero
      stale reuse in the affected phase(s), no crash (recompute is correct behavior).
- [ ] The translator digest change is confined to the added leading `model` field
      (verified by a test pinning the new field order against a hand-computed digest —
      not by the round-trip tests, which would pass under any deterministic digest).
- [ ] Namespace invariant holds over a file populated by all four phases.

## Context & references

```yaml
# Files to read/follow in this repo:
- breviabook/persistence/fingerprint.py      # THE shared helper (previous PRP)
- breviabook/condense/condenser.py           # _cached_chunk + reuse rules — THE pattern
- breviabook/condense/synthesizer.py         # synthesize_chapter loop, _synth_pass, _result
- breviabook/condense/prompts.py             # build_synthesize_messages — audited inputs
- breviabook/images/vision_ranker.py         # _Verdict, _rank, parse-failure-keeps behavior
- breviabook/translate/translator.py         # _batch_fingerprint (model gap), _cached_batch
- breviabook/pipeline.py                     # phase wiring, CondenseResult, warnings
- tests/test_condenser.py                    # fingerprint matrix to mirror per phase
- tests/test_pipeline.py                     # PhaseAwareProvider, RoutingProvider patterns
```

## Implementation blueprint

1. `checkpoint.py` docstring: namespace contract + payload shapes + edge note.
2. `translator.py`: `_batch_fingerprint(batch, model, target_lang, …)` — new leading
   field; update the one call site and the tests that compute fingerprints by hand.
3. `synthesizer.py`: fingerprint + `_cached_chapter` + payload wrap + `synthesis_failed`
   + `reused_chapters` + docstring note on trim-degradation caching.
4. `vision_ranker.py`: `Verdict(BaseModel)` + fingerprint + `_cached_verdict` + payload
   wrap + skip caching unparsed verdicts + `reused_images`.
5. `pipeline.py`: wire checkpoints and counters, warnings, result fields, CLI rows.
6. Tests.

### New / changed files

- `breviabook/persistence/checkpoint.py` — docstring only.
- `breviabook/translate/translator.py` — fingerprint gains `model`.
- `breviabook/condense/synthesizer.py` — checkpointing + `synthesis_failed`.
- `breviabook/images/vision_ranker.py` — checkpointing + `Verdict` model.
- `breviabook/pipeline.py`, `breviabook/cli.py` — wiring + reporting.
- `tests/test_synthesizer.py`, `tests/test_vision_ranker.py`,
  `tests/test_translator.py`, `tests/test_pipeline.py` — matrices + e2e.

## Validation gates (must all pass)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict breviabook
uv run pytest -q
uv run pip-licenses --partial-match --fail-on "General Public License;GPL" --ignore-packages pyphen
```

### Feature-specific checks

- [ ] Full run with `translate_to` + `--rank-images`, then same-config `--resume`: zero
      condense calls, zero synthesis calls, zero translate calls, zero vision calls;
      all four reused counters equal their totals.
- [ ] Crash simulation: provider raises mid-translation; resume with a healthy provider
      → condense and synthesis make no calls; translation re-runs only the incomplete
      batches; run completes.
- [ ] Changed `--model` → all four phases recompute (this now covers the translator too).
- [ ] Changed `--target-ratio` → condense, synthesis, translation recompute (transitive).
- [ ] Vision: same image id with different bytes → re-ranked; edited neighbor text →
      re-ranked; parse-failure verdict not cached, retried on resume.
- [ ] Synthesis: smooth-pass failure not cached, retried on resume, warning emitted;
      changed `min_target_tokens`-clamped chapter (tiny chapter) still fingerprint-safe.
- [ ] Namespace invariant test: every key in a fully populated file is either
      `^ch\d+-\d+$` or starts with `syn:` / `tr:` / `img:`.
- [ ] Corrupt inner payloads (`chapter` / `verdict` failing validation) → recompute.
- [ ] Translator: model-change invalidation test; hand-computed digest pins field order.

## Acceptance criteria

- [ ] No paid phase repeats paid work on a same-config resume — the leak is closed in all
      three remaining phases, and the translator's model blind spot is fixed.
- [ ] Checkpoint key namespacing is collision-free by construction, documented, and
      enforced by a test.
- [ ] All validation gates green.

## Confidence score

8/10 — The pattern is established and two of the three phases are small wirings; the
delicate points are the deliberate translator digest change (existing hand-computed
fingerprints in tests must be updated) and getting the synthesis fingerprint's
`input_tokens` field right (it is the original chunk size, not derivable downstream).
