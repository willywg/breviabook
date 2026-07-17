# PRP: Refactor `condense_book` into readable pieces (zero behavior change)

> Product Requirement Prompt for **BreviaBook**. Operational-debt follow-up after
> `feat--concurrency.md` (commit `0d9d1a5`). Source of truth:
> [docs/ROADMAP.md](../docs/ROADMAP.md) §5, §6, §14. Operating rules:
> [CLAUDE.md](../CLAUDE.md).

## Goal

Decompose `breviabook/pipeline.py::condense_book` — today a **193-line** god-function with
**19 keyword-only parameters** and **two full pipelines** in one `if/else` body — into small,
named private helpers (and, if useful, an internal run-state dataclass) **without changing any
observable behavior**. The public signature used by the CLI and the test suite stays stable.
After the change: same 281 tests green, zero assertion edits, same checkpoints / stems /
warnings / counters / phase order.

## Why

- `concurrency` (commit `0d9d1a5`) became parameter **#19**. The function already hosted two
  complete paths (`translate_only` vs condense) plus a shared vision/select/render tail; more
  flags will keep landing here unless the body is split.
- The two branches share parse → (branch) → optional vision → `ImageSelector` → render →
  `CondenseResult`, but that shared skeleton is hard to see inside one 190-line body.
- This is pure maintainability debt from the architecture audit queue. No user-facing feature;
  the value is that the next operational PR (usage-on-Protocol, Ollama retry) can touch a
  smaller surface without re-reading both pipelines every time.

## Baseline (verified against the tree at `0d9d1a5`)

Commands run before writing this PRP:

```text
$ git rev-parse --short HEAD
0d9d1a5

$ uv run pytest -q
281 passed, 1 skipped in ~3s

$ uv run pytest --collect-only -q | tail -1
282 tests collected
```

`condense_book` keyword parameters (counted from the live signature in
`breviabook/pipeline.py` lines 215–236):

| # | name | default |
|---|---|---|
| 1 | `input_path` | required |
| 2 | `out_dir` | required |
| 3 | `formats` | required |
| 4 | `provider` | required |
| 5 | `model` | required |
| 6 | `target_ratio` | `0.30` |
| 7 | `chunk_tokens` | `2000` |
| 8 | `resume` | `False` |
| 9 | `checkpoint_path` | `None` |
| 10 | `translate_to` | `None` |
| 11 | `source_lang` | `None` |
| 12 | `glossary` | `None` |
| 13 | `rank_images` | `False` |
| 14 | `manual_toc` | `None` |
| 15 | `infer_pages` | `20` |
| 16 | `log` | `_noop` |
| 17 | `reporter` | `None` |
| 18 | `translate_only` | `False` |
| 19 | `concurrency` | `DEFAULT_CONCURRENCY` |

Body layout (line numbers from `breviabook/pipeline.py` at HEAD):

| Region | Lines | Role |
|---|---|---|
| Signature + docstring | 215–241 | public API |
| Shared setup | 242–263 | concurrency guard, reporter default, formats, parse, `input_tokens` |
| `translate_only` branch | 265–292 | stem, checkpoint, `Translator`, reuse/warnings |
| `else` condense branch | 293–360 | chunk → condense → synthesize → optional translate-after-condense |
| Shared tail | 362–405 | optional vision rank, `ImageSelector`, render loop, `CondenseResult` |

Call sites that must keep working **without signature change** (preferred):

- `breviabook/cli.py` — `condense` (~L285) and `translate` (~L415), both keyword-only.
- `tests/test_pipeline.py` — **24** `condense_book(` call sites (including `**_FOUR_PHASE`).
- `tests/test_usage.py` — **2** call sites.
  (Verified with `rg`/count on the tree; total test call sites = 26.)

`getattr(..., "usage", None)` sites today (optional cleanup, see Out of scope):

- `breviabook/pipeline.py:392`
- `breviabook/cli.py:276` and `breviabook/cli.py:406`

## Scope

**In scope:**

- Extract the `translate_only` branch and the condense branch into private helpers in
  `pipeline.py` (same module — stage modules must still not import each other; only
  `pipeline.py` integrates them).
- Extract the shared post-branch tail (vision → image-select → render → result assembly)
  into one or more private helpers so both paths share one implementation, not a copy.
- Optionally introduce a **private** `@dataclass` for mid-run mutable state (working doc,
  stem, checkpoint, counters, warnings) so helpers do not grow 12-arg signatures.
- Keep the **public** `async def condense_book(*, …) -> CondenseResult` signature
  byte-compatible (names, defaults, keyword-only). CLI and tests call it unchanged.
- Preserve every behavioral contract listed under “Behavior contracts” below.
- Leave `estimate_condense` alone unless a one-line import/helper move is forced by the
  refactor (it is not a god-function; do not expand scope into dry-run).

**Out of scope:**

- Changing pipeline order, checkpoint paths/filenames, fingerprint fields, key namespacing,
  concurrency semantics, or progress phase names/totals.
- CLI dedup / helper extraction in `cli.py` (already done).
- Moving `usage` onto `LLMProvider` Protocol (the three `getattr` sites) — only if it drops
  in with zero behavior risk; **default recommendation: leave for the next PRP** (see
  design decision below).
- Adding retry/backoff to `OllamaProvider`.
- New features, new CLI flags, new tests *required* for green (optional characterization
  tests are allowed but not required if the existing suite fully covers the contracts).
- Editing assertions in existing tests. Call-site updates are allowed **only** if the
  public signature is intentionally changed (this PRP recommends against that).

## Non-negotiable constraints

- [ ] Clean-room only; no code copied/translated from reference repos.
- [ ] No new runtime dependencies; no GPL/AGPL deps (`ebooklib`, `PyMuPDF`/`fitz` forbidden).
- [ ] IR invariants unchanged: `code` / `image` never summarized or split; tables unsplit.
- [ ] Pure refactor: **281 passed, 1 skipped** before and after; zero new failures.
- [ ] Do not modify existing test assertions. Prefer zero test-file edits.
- [ ] Do not commit secrets, `*.sqlite`, or the untracked `AGENTS.md`.
- [ ] `mypy --strict breviabook` remains clean; helpers fully typed.
- [ ] Single implementation commit after the five quality gates are green.

## Design decision (with rejected alternatives)

### Decision: keep the public signature; extract private path helpers + private run state

**Do this:**

1. Leave `condense_book`'s 19 keyword-only parameters exactly as they are. The function
   becomes a thin orchestrator: validate → parse → dispatch → finish.
2. Introduce a private `@dataclass` (name suggestion: `_RunState`) holding the mutable
   mid-pipeline values that today are local variables:
   - `working_doc: Document`
   - `stem: str`
   - `run_checkpoint: CheckpointManager | None`
   - `warnings: list[str]`
   - `chunks_total`, `chunks_reused`, `chapters_reused`, `batches_reused`, `images_reused: int`
   - `input_tokens: int` (set at parse time; needed for `CondenseResult`)
   - `translate_only: bool` (for the result flag)
3. Extract at least these private async/sync helpers (exact names free; responsibilities fixed):

   | Helper | Responsibility (must match current code) |
   |---|---|
   | `_run_translate_only(...)` | L265–292: require `translate_to`; stem `{stem}-{lang}`; own checkpoint file; `clear()` unless resume; `Translator.translate_document`; set `batches_reused` + untranslated warning |
   | `_run_condense(...)` | L293–360: chunk; stem `{stem}-condensed`; checkpoint; condense → synthesize → `synthesized_to_document`; optional post-synthesis translate on the **same** checkpoint; warnings for longer-output / condense-failed / synthesis-failed / untranslated |
   | `_maybe_rank_images(...)` | L362–376: vision capability check; `VisionRanker.rank`; `images_reused` |
   | `_select_and_render(...)` | L378–390: `ImageSelector.select` → **`final_doc`**; render each format from that same doc; swallow `RuntimeError` per format into warnings. **Must return `(output_files, final_doc)`** (or store `final_doc` on `_RunState` before render) so the orchestrator computes `output_tokens=_document_tokens(final_doc)` on the **post-selection** document — never on pre-selection `working_doc`. |
   | (thin) `condense_book` | L242–263 setup + dispatch + assemble `CondenseResult` including `getattr(provider, "usage", None)` and `output_tokens` from the post-selection `final_doc` |

4. Helpers take only what they need (state object + the few immutable inputs: `provider`,
   `model`, `concurrency`, `reporter`, …). Prefer passing `_RunState` over re-listing every
   counter as a separate return tuple.

**Why not a public `PipelineConfig` dataclass replacing the 19 kwargs?**

- The only external callers are the CLI (2 sites) and tests (~24 sites). Replacing kwargs
  with a config object forces a wide call-site churn for zero user-visible gain.
- A public config also freezes a second API surface that must stay in sync with Typer
  options. Keeping kwargs at the boundary and a private state object *inside* the body
  gets the readability win without a migration.

**Why not only extract helpers without a state dataclass?**

- Acceptable fallback if the dataclass feels over-engineered. But each helper would then
  return a multi-field tuple (`working_doc, stem, checkpoint, warnings, counters…`) and
  the orchestrator re-unpacks it — that is how the current locals got messy. A single
  private state object is the lower-noise option under `mypy --strict`.

**Why leave `usage → Protocol` for later (default)?**

- `LLMProvider` is a `@runtime_checkable` Protocol with required `name` + `generate`.
  Adding optional `usage` is not free: Protocol members are structural requirements;
  test doubles (`ScriptedProvider`, `PhaseAwareProvider`, `AllPhaseProvider`,
  `TranslateOnlyProvider`, …) do not define `usage`, and `getattr` is what lets them
  stay minimal.
- A clean design needs either a second Protocol (`UsageTrackingProvider`), a
  `typing.runtime_checkable` optional attribute pattern, or a small helper
  `def provider_usage(p: LLMProvider) -> Usage | None` used in the three sites — that is
  a focused follow-up, not required to make `condense_book` readable.
- **Optional micro-win allowed in this PR:** extract
  `def _provider_usage(provider: LLMProvider) -> Usage | None` in `pipeline.py` and use it
  at the single pipeline site. Do **not** expand into `cli.py` / `base.py` unless the
  implementer is sure all five gates stay green with zero test assertion changes.

## Behavior contracts (must not change — and what protects them)

Every claim below was checked against the named test. If a refactor broke the contract,
that test would fail.

| Contract | Where it lives today | Protected by |
|---|---|---|
| Keyword API accepts the fixture and writes `sample-condensed.{md,epub}` | public `condense_book` | `test_end_to_end_writes_md_and_epub` |
| Unsupported suffix → `ValueError` | `_check_supported` via parse | `test_unsupported_input_raises` |
| `resume=True` reuses condense chunks (zero new condense calls) | condense branch + checkpoint | `test_resume_skips_provider_for_done_chunks` |
| Changed `target_ratio` invalidates condense cache | fingerprint (untouched) + wiring | `test_resume_with_changed_target_ratio_recomputes` |
| Fresh run (`resume=False`) clears checkpoint | both branches `clear()` | `test_fresh_run_clears_stale_checkpoint` |
| PDF input works end-to-end | shared parse | `test_pdf_input_end_to_end` |
| Condense+translate runs | condense branch optional translate | `test_translation_end_to_end` |
| `--rank-images` needs `VisionProvider` | shared tail | `test_rank_images_requires_vision_provider` |
| Vision can drop images | shared tail | `test_rank_images_drops_via_vision` |
| PDF render `RuntimeError` → warning, other formats kept | render loop | `test_pdf_render_failure_is_skipped_with_warning` |
| `translate_only=True` ⇒ `chunks_total==0`, `translate_only` flag | translate branch | `test_translate_only_no_condense_calls` |
| `translate_only` without `translate_to` ⇒ `ValueError` | translate branch guard | `test_translate_only_requires_translate_to` |
| Code fences survive translate-only | translate + render | `test_translate_only_preserves_code_and_images` |
| Translate-only resume: 0 calls, `batches_reused` | translate checkpoint stem | `test_translate_only_resume_skips_done_batches` |
| ImageSelector still runs on translate-only | shared tail | `test_translate_only_image_selector_still_runs` |
| Unparseable translate → warning | both translate wirings | `test_translate_only_untranslated_warning` |
| Full four-phase resume: 0 calls all phases; reuse counters | condense path + shared vision | `test_full_run_resume_makes_zero_calls_in_all_four_phases` |
| Model change recomputes all four phases | fingerprints + wiring | `test_changed_model_recomputes_all_four_phases` |
| Checkpoint keys namespaced `chN-M` / `syn:` / `tr:` / `img:` | one file per condense run | `test_checkpoint_keys_are_namespaced_by_phase` |
| `CondenseResult.usage` from provider when present | result assembly | `tests/test_usage.py` |
| `concurrency < 1` rejected | setup guard | concurrency tests in `tests/test_concurrency.py` / pipeline |

Stem / checkpoint path rules to preserve verbatim:

- Condense run: `stem = f"{input_path.stem}-condensed"`;
  default checkpoint `out_dir / ".breviabook" / f"{stem}.jsonl"`.
- Translate-only: `stem = f"{input_path.stem}-{translate_to.lower().replace(' ', '-')}"`;
  default checkpoint same pattern under that stem.
- Condense+translate (not translate-only): translation records go into the **condense**
  checkpoint (`run_checkpoint` / same manager), not a separate file.
- `rank_images` uses `run_checkpoint` set by whichever branch ran.

Phase reporter order to preserve:

1. `"Parse"` (total=1) — always
2. Either `"Translate"` (translate-only) **or** `"Condense"` → `"Synthesize"` → optional
   `"Translate"` (condense path)
3. Optional `"Rank images"` (total = `vision_ranker.rankable_count(working_doc)`)
4. `"Render"` (total = number of formats)

## Context & references

```yaml
# Read before implementing:
- docs/ROADMAP.md                 # §5 pipeline order; §6 package layout; §14 license
- CLAUDE.md                       # quality gates, clean-room, IR invariants
- breviabook/pipeline.py          # condense_book L215–405 — the only file that must change
- breviabook/cli.py               # L285–301 and L415–430 call sites (read-only if signature kept)
- tests/test_pipeline.py          # full behavioral contract suite
- tests/test_usage.py             # CondenseResult.usage
- tests/test_concurrency.py       # concurrency guard + phase wiring
- PRPs/feat--concurrency.md       # immediately prior debt item; do not regress
- PRPs/feat--checkpoint-remaining-phases.md  # one-file namespaced checkpoint rules
- PRPs/feat--translate-command.md # translate_only branch semantics
```

Study patterns only; never copy from TranslateBooksWithLLMs, ollama-ebook-summary, or
OllamaBook-Summarize.

Existing style cues inside `pipeline.py`:

- Module already uses `@dataclass` for `CondenseResult` and `Estimate` — a private
  `_RunState` fits the same pattern.
- Private helpers already use a leading underscore (`_parse_input`, `_render`,
  `_document_tokens`, `_noop`).
- Stage modules (`condense/`, `translate/`, `images/`) are imported only here; keep it that
  way. Do not move orchestration into stage packages.

## Implementation blueprint

1. **Re-baseline.** From a clean tree at `0d9d1a5` (or current `main` if already there):
   ```bash
   uv run pytest -q
   ```
   Record the summary line. It must read `281 passed, 1 skipped` (or a higher passed count
   only if unrelated tests were added later — never lower).

2. **Sketch the private state dataclass** at module level near `CondenseResult` (not
   exported from `__init__.py`):
   ```python
   @dataclass
   class _RunState:
       working_doc: Document
       stem: str
       input_tokens: int
       run_checkpoint: CheckpointManager | None = None
       warnings: list[str] = field(default_factory=list)
       chunks_total: int = 0
       chunks_reused: int = 0
       chapters_reused: int = 0
       batches_reused: int = 0
       images_reused: int = 0
       translate_only: bool = False
   ```
   Adjust field set only if a field is truly unused after the extract; do not invent fields
   the current body does not already track.

3. **Extract `_run_translate_only`.** Move the body of `if translate_only:` (L265–292)
   **verbatim in behavior**:
   - Raise `ValueError("translate_only requires translate_to")` when `translate_to` is
     falsy (exact message — `test_translate_only_requires_translate_to` matches it).
   - Build stem with `.lower().replace(" ", "-")` on the target language.
   - Default checkpoint path; `CheckpointManager`; assign `run_checkpoint`; `clear()` when
     not `resume`.
   - Construct `Translator` with `checkpoint=tr_checkpoint` (and glossary/source_lang).
   - `await translator.translate_document(..., concurrency=concurrency, on_progress=...)`.
   - Set `batches_reused` from `translator.reused_batches`.
   - Append the untranslated-units warning with the **same** wording as today.
   - Return/update `_RunState` with `working_doc`, `stem`, counters, `translate_only=True`.

4. **Extract `_run_condense`.** Move the `else` body (L293–360) with identical order:
   - `Chunker(chunk_tokens).chunk(doc)` → `chunks_total`.
   - `reporter.note(f"{chunks_total} chunks")` stays (today it is inside the branch).
   - Checkpoint path default to condensed stem; `clear()` when not resume.
   - `Condenser.condense` → longer-output + condense-failed warnings (same f-strings).
   - `Synthesizer.synthesize` with `n_chapters = len({cc.chapter_index for cc in condensed})`
     as the phase total.
   - `synthesized_to_document(doc, chapters)`.
   - If `translate_to`: build `Translator` with the **same** `checkpoint` object; translate
     `working_doc`; set `batches_reused`; untranslated warning.
   - Do not run vision or render here.

5. **Extract shared tail helpers.**
   - `_maybe_rank_images(state, *, provider, model, concurrency, reporter)` — keep the
     exact `ValueError` message for non-vision providers (includes
     `getattr(provider, "name", "provider")`).
   - `_select_and_render(state, *, formats, out_dir, reporter) -> tuple[list[Path], Document]`:
     ```python
     selected = ImageSelector().select(state.working_doc)
     final_doc = selected.document          # POST-selection — oracle for output_tokens
     # render loop uses final_doc (not working_doc)
     return output_files, final_doc
     ```
     **Critical:** today `final_doc` feeds **both** the render loop and
     `output_tokens=_document_tokens(final_doc)`. Returning only `list[Path]` would drop
     that reference; computing tokens from pre-selection `working_doc` would silently
     change the number (and likely still pass tests that do not assert exact token
     counts). Thread the post-selection doc explicitly.

6. **Rewrite `condense_book` as orchestrator** (behavior-identical control flow):
   ```text
   validate concurrency
   default reporter
   validate formats; mkdir out_dir
   phase Parse → _parse_input → input_tokens → advance → note chapters/tokens
   if translate_only:
       state = await _run_translate_only(...)
   else:
       state = await _run_condense(...)
   await _maybe_rank_images(...)
   output_files, final_doc = _select_and_render(...)
   usage = getattr(provider, "usage", None)
   return CondenseResult(..., output_tokens=_document_tokens(final_doc), ...)
   ```
   Field mapping into `CondenseResult` must stay 1:1 with L393–405.

7. **Do not touch** stage modules, `cli.py` (if signature kept), checkpoint/fingerprint
   code, or test assertions.

8. **Type-check and format as you go.** Helpers must be fully annotated for
   `mypy --strict`. Prefer `from __future__ import annotations` (already present).

9. **Validate.** Run the five gates. Compare pytest summary to the baseline from step 1.

10. **Single commit** only after gates are green (message suggestion):
    `refactor(pipeline): split condense_book into path helpers`

### New / changed files

- `breviabook/pipeline.py` — **only required production change**: private helpers +
  optional `_RunState`; `condense_book` becomes an orchestrator; public signature unchanged.
- `tests/*` — **no edits expected**. If an accidental public-signature change is made,
  update call sites only (not assertions) — but that path is rejected by this PRP.
- `PRPs/feat--refactor-condense-book.md` — this file (planning only; already present when
  executing).

### Suggested final shape (illustrative, not prescribed line-for-line)

```python
async def condense_book(*, input_path: Path, ... concurrency: int = DEFAULT_CONCURRENCY) -> CondenseResult:
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")
    ...
    doc = await _parse_input(...)
    input_tokens = count_document_tokens(doc)
    ...
    if translate_only:
        state = await _run_translate_only(doc, input_tokens=input_tokens, ...)
    else:
        state = await _run_condense(doc, input_tokens=input_tokens, ...)
    await _maybe_rank_images(state, provider=provider, model=model, ...)
    output_files, final_doc = _select_and_render(state, formats=fmts, out_dir=out_dir, reporter=reporter)
    usage = getattr(provider, "usage", None)
    return CondenseResult(
        output_files=output_files,
        input_tokens=state.input_tokens,
        output_tokens=_document_tokens(final_doc),  # POST ImageSelector — same as pre-refactor
        ...
    )
```

Implementers: copy behavior from the current body, not from this sketch. The sketch is a
shape guide; the live `pipeline.py` is the oracle.

## Validation gates (must all pass)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict breviabook
uv run pytest -q
uv run pip-licenses --partial-match --fail-on "General Public License;GPL" --ignore-packages pyphen
```

### Feature-specific checks

- [ ] Before/after `uv run pytest -q` summary is identical: **`281 passed, 1 skipped`**
      (re-count with `uv run pytest --collect-only -q` if the suite has grown; passed count
      must not drop and no new failures).
- [ ] `git diff` production changes are confined to `breviabook/pipeline.py` (plus this PRP
      if it is committed with the work — prefer committing the PRP only if the repo tracks
      PRPs; follow whatever prior feat PRs did).
- [ ] Public signature of `condense_book` still has the same 19 keyword parameters and
      defaults (`rg -n "async def condense_book" -A 25 breviabook/pipeline.py`).
- [ ] No stage-module imports each other; only `pipeline.py` wires them
      (`rg "from breviabook\.(condense|translate|images)" breviabook/condense breviabook/translate breviabook/images` stays empty of cross-imports).
- [ ] Exact error strings preserved:
      - `translate_only requires translate_to`
      - `concurrency must be at least 1`
      - `--rank-images needs a vision-capable provider/model ...`
- [ ] Stem rules preserved (spot-check via existing tests that assert output filenames
      `sample-condensed.*` and `sample-spanish.*`).
- [ ] No real LLM calls in tests; mock/scripted providers only.
- [ ] Optional: if `_provider_usage` helper is added, it remains a pure `getattr` with the
      same `isinstance(..., Usage)` guard — no Protocol change required in this PR.

## Acceptance criteria

- [ ] `condense_book` body is an orchestrator; translate-only and condense paths live in
      named private helpers; shared vision/select/render is not duplicated.
- [ ] Public API (signature + `CondenseResult` fields + defaults) unchanged.
- [ ] CLI and all existing tests call `condense_book` the same way; **no assertion edits**.
- [ ] Observable behavior identical: phase order, checkpoints, stems, warnings, counters,
      concurrency forwarding, usage attachment, render skip-on-`RuntimeError`.
- [ ] All five validation gates green; pytest summary matches baseline.
- [ ] One commit; no secrets; no `AGENTS.md` added.

## Confidence score

9/10 — Pure extract-method refactor with a dense existing contract suite (especially
`test_pipeline.py` four-phase resume + translate-only matrix). Residual risk is accidental
reorder of side effects (e.g. moving `reporter.note` for chunk count, or wiring translate-
after-condense to a different checkpoint instance); the blueprint pins those to the live
line ranges so a careful implementer should one-pass it.
