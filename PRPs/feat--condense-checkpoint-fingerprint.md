# PRP: Content/config fingerprint for condense checkpoints

> Product Requirement Prompt for **BreviaBook**. Correctness fix for `--resume`
> (condense path). Mirrors the translator's `_batch_fingerprint` pattern (PRP 010).
> Operating rules: [CLAUDE.md](../CLAUDE.md).

## Goal

Condense checkpoints are keyed only by positional chunk id (`ch{i}-{n}`, chunker.py) and
the file is named by input stem (`{stem}-condensed.jsonl`). A `--resume` after changing
`--chunk-tokens`, `--target-ratio`, `--model`, or the input book itself (same stem)
**silently reuses stale condensed output**. Add a SHA-1 source fingerprint to every
checkpoint record — same pattern the translator already uses — so stale records are
recomputed instead of reused.

## Why

- Paid runs produce silently wrong books today: resume with a different chunk size or
  model and the old condensed text is reused with no warning.
- The asymmetry is the smell: translator checkpoints fingerprint (target lang + source
  lang + glossary + content) since PRP 010; the condenser validates nothing. Both paths
  should share one fingerprint mechanism.

## Scope

**In scope:**
- New `breviabook/persistence/fingerprint.py`: shared `Fingerprint` helper implementing
  the translator's exact pattern — incremental SHA-1 (`usedforsecurity=False`) over
  NUL-separated UTF-8 fields (NUL separation prevents `("ab","c")` ≡ `("a","bc")`).
- `condense/condenser.py`:
  - `_chunk_fingerprint(chunk, model, target_ratio)` — fields: `model`,
    `repr(target_ratio)`, canonical JSON of the chunk's block list
    (`json.dumps(blocks_dump, sort_keys=True, ensure_ascii=False)`, blocks **in order** —
    sequence is semantic, unlike the translator's dict batches).
  - Record payload becomes `{"source_hash": fp, "chunk": cc.model_dump(mode="json")}`.
  - `_cached_chunk()` mirror of the translator's `_cached_batch()`: reuse only when the
    payload's `source_hash` matches **and** the inner `chunk` passes
    `CondensedChunk.model_validate` (ValidationError → treat as stale, recompute).
  - Only successful chunks are cached: a `condense_failed=True` result is **not**
    recorded, mirroring the translator's "only complete batches are cacheable" rule —
    a resume is precisely the chance to retry a transient failure.
  - `Condenser.reused_chunks` counter (mirrors `Translator.reused_batches`).
- `translate/translator.py`: refactor `_batch_fingerprint` to use the shared
  `Fingerprint` helper — same digests as before (pure refactor, no behavior change).
- `pipeline.py`: `chunks_reused` now comes from `condenser.reused_chunks`, not
  `len(checkpoint.results())` (which counts stale records and over-reports).
- `persistence/checkpoint.py`: module docstring notes the two payload shapes
  (`{"source_hash", "chunk"}` / `{"source_hash", "translations"}`).
- Tests.

**Out of scope:**
- Checkpointing synthesis / vision-ranking / translate-after-condense (separate PRP).
- Wiring `Chunk.prev_context` into the prompt (dead data today). Note in the condenser
  docstring: if it ever reaches the prompt, it must join the fingerprint.
- A prompt-version field (translator doesn't have one either; parity beats one-sided
  cleverness — revisit both together if prompts churn).
- `reasoning_effort` (lives in `provider.extra_opts`). It changes the output and is a
  peer of `model`, so its absence is a deliberate decision, not an oversight. Deferred
  because capturing it would require `getattr(provider, "extra_opts", {})` — the smell
  this PRP removes — and the translator has the same gap. Parity with the prompt-version
  item above: if either is ever revisited, revisit both (and both pipelines) together.
- Old bare-format checkpoint records (no `source_hash`): treated as stale and recomputed
  once. Checkpoints are disposable job state; no migration.

## Non-negotiable constraints (CLAUDE.md)

- [ ] No new runtime deps (`hashlib`, `json` are stdlib).
- [ ] Same-config resume must make **zero** provider calls (proved with a call-counting
      mock provider).
- [ ] Any change of model / target_ratio / chunk_tokens / book content → zero stale reuse,
      no crash, no warning needed (recompute is the correct behavior).
- [ ] Translator digests unchanged after the refactor (regression-guarded by existing
      translator checkpoint tests).

## Context & references

```yaml
# Files to read/follow in this repo:
- breviabook/translate/translator.py   # _batch_fingerprint + _cached_batch — THE pattern
- breviabook/condense/condenser.py     # condense() reuse logic (lines ~77-89), record site
- breviabook/condense/chunker.py       # chunk ids ch{i}-{n}, prev_context (unused by prompt)
- breviabook/persistence/checkpoint.py # append-only JSONL store, torn-line tolerance
- breviabook/pipeline.py               # lines ~289-294: checkpoint path + reused counting
- tests/test_translator.py             # checkpoint matrix: language/glossary change,
                                       #   partial batch, incomplete record — mirror this
- tests/test_condenser.py              # existing condenser tests incl. resume at unit level
```

## Implementation blueprint

1. `persistence/fingerprint.py`:
   ```python
   class Fingerprint:
       """Incremental SHA-1 over NUL-separated fields (shared checkpoint pattern)."""
       def __init__(self) -> None: self._h = hashlib.sha1(usedforsecurity=False)
       def field(self, value: str) -> None:
           self._h.update(value.encode("utf-8")); self._h.update(b"\0")
       def hexdigest(self) -> str: return self._h.hexdigest()
   ```
2. `translator.py`: `_batch_fingerprint` rebuilt on `Fingerprint`, field order unchanged
   (target, source, glossary, sorted uid/text pairs) → identical digests.
3. `condenser.py`: `_chunk_fingerprint` + `_cached_chunk` + payload wrap + skip caching
   failed chunks + `reused_chunks`.
4. `pipeline.py`: read `condenser.reused_chunks`.
5. Docstrings (checkpoint payload shapes; condenser note re: `prev_context`).
6. Tests.

### New / changed files

- `breviabook/persistence/fingerprint.py` — new shared helper.
- `breviabook/condense/condenser.py` — fingerprint, payload wrap, reuse validation,
  `reused_chunks`, no caching of failed chunks.
- `breviabook/translate/translator.py` — `_batch_fingerprint` on shared helper.
- `breviabook/pipeline.py` — accurate `chunks_reused`.
- `breviabook/persistence/checkpoint.py` — docstring only.
- `tests/test_fingerprint.py` — new.
- `tests/test_condenser.py`, `tests/test_pipeline.py` — fingerprint resume matrix.

## Validation gates (must all pass)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict breviabook
uv run pytest -q
uv run pip-licenses --partial-match --fail-on "General Public License;GPL" --ignore-packages pyphen
```

### Feature-specific checks

- [ ] Same config + `--resume` → all chunks reused, provider call count stays 0.
- [ ] Changed `--chunk-tokens` (different chunking, same positional ids) → all recomputed.
- [ ] Changed `--target-ratio` or `--model` → all recomputed.
- [ ] Different book, same input stem → all recomputed (content hash differs).
- [ ] Old bare-format record (no `source_hash`) → recomputed, no crash.
- [ ] `condense_failed=True` chunk is not recorded; a resume retries it.
- [ ] Corrupt inner payload (fails `model_validate`) → treated as stale, recomputed.
- [ ] `chunks_reused` reflects actual reuses, not total records in the file.
- [ ] Translator checkpoint tests pass unchanged (digest stability).
- [ ] `test_pipeline.py` gains a *real* end-to-end resume test (calls `condense_book`
      twice with resume=True, second run makes zero provider calls) — the existing test
      only reopens the file and asserts non-empty.

## Acceptance criteria

- [ ] A stale condense checkpoint can never be silently reused: any config or content
      change is detected by the fingerprint and recomputed.
- [ ] Condenser and translator share one fingerprint implementation and one
      reuse-validation shape; the asymmetry is gone.
- [ ] All validation gates green.

## Confidence score

9/10 — The pattern is proven in the translator and the touch points are small; the only
delicate bit is keeping translator digests byte-identical after the refactor (guarded by
its existing checkpoint matrix).
