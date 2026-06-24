# PRP: Phase 12 — Polish

> Product Requirement Prompt for **Brevia**. Source of truth: [docs/ROADMAP.md](../docs/ROADMAP.md) §8, §10 (Phase 12), §13.
> Operating rules: [CLAUDE.md](../CLAUDE.md). Final roadmap phase.

## Goal

Final polish: (1) `--dry-run` reports an approximate **per-provider cost** before launching;
(2) skip wasteful synthesis/trim work on already-small chapters (deferred fix); (3) a real
user **README**. Logging already flows through the rich `log` callback.

## Why

- §13.5 acceptance: dry-run should report estimated tokens **and approximate cost**.
- The usage report exposed that tiny chapters trigger pointless trim passes (extra LLM calls).
- The project needs a usable README before release.

## Scope

**In scope:**
- `llm/pricing.py`: `estimate_cost(route, model, prompt_tokens, completion_tokens) -> float|None`
  (litellm price map, best-effort).
- `pipeline.estimate_condense`: estimate prompt/completion tokens across passes
  (condense + synth [+ translate], with overhead) and a cost; new `Estimate` fields. CLI dry-run
  prints the cost (or "n/a" for unpriced/local models).
- `condense/synthesizer.py`: floor `target_tokens` at a minimum and **skip synthesis for a
  single chunk already within budget** (no smoothing/trim call) — the deferred guard.
- `README.md`: features, install, quickstart, CLI options, providers/env, examples, license.
- Tests updated/added.

**Out of scope:** new output formats; provider additions.

## Non-negotiable constraints (CLAUDE.md)

- [ ] `--dry-run` still performs NO LLM call (pricing uses litellm's static price map only).
- [ ] The trim guard must not change correct behavior for real (multi-chunk / over-budget)
      chapters — only avoid wasted calls on small ones.
- [ ] Cost is clearly labeled approximate; unpriced models show "n/a", never a wrong number.

## Context & references

```yaml
- brevia/pipeline.py            # estimate_condense / condense_book
- brevia/condense/synthesizer.py # length-control loop to guard
- brevia/llm/providers/litellm_base.py # completion_cost (post-run) for reference
- litellm.cost_per_token(model="<route>/<model>", prompt_tokens=, completion_tokens=)
- memory: brevia-phase12-todos (the deferred trim guard)
```

## Design

- **Cost estimate:** with `ratio` and `chunks`, approximate
  `prompt ≈ input + out + (out if translate) + overhead*chunks`,
  `completion ≈ out * (2 + translate)`, where `out = input*ratio`; cost via `estimate_cost`.
- **Trim guard:** `target = max(round(ratio*input), MIN_TARGET_TOKENS)`; if a chapter is a
  single chunk and its current tokens ≤ target, return it unchanged (0 calls); otherwise the
  existing smoothing + bounded trim loop runs.

## Implementation blueprint

1. `llm/pricing.py`.
2. `synthesizer.py`: `min_target_tokens`, floor, single-chunk-under-budget short-circuit.
3. `pipeline.py`: `Estimate` fields + cost in `estimate_condense(provider_name, model, translate_to)`.
4. `cli.py`: pass provider/model/translate_to to the estimate; print cost row.
5. `README.md`.
6. Tests: `tests/test_pricing.py`; update `tests/test_synthesizer.py`; extend estimate/CLI tests.

## Validation gates (must all pass)

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict brevia
uv run pytest -q && uv run pip-licenses --fail-on "GPL"
```

### Feature-specific checks

- [ ] `estimate_cost` returns a float for a priced model, None for an unknown/local one.
- [ ] dry-run `Estimate` includes prompt/completion token estimates and a cost (or None); no LLM call.
- [ ] A single small chapter is returned unchanged with zero synthesis calls; multi-chunk and
      over-budget chapters still smooth/trim as before (bounded).
- [ ] `target_tokens` is floored at the minimum for tiny inputs.
- [ ] README documents install, usage, providers, and PDF system-lib requirement.

## Acceptance criteria

- [ ] `brevia condense book.epub --dry-run` shows estimated tokens + approximate cost, no LLM call.
- [ ] No more wasted trim passes on small chapters.
- [ ] All five validation gates green. Brevia is feature-complete per the spec.

## Confidence score

8/10 — Mostly mechanical; the synthesizer guard requires updating several existing tests to the
new (correct) behavior.
