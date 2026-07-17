# PRP: Bounded intra-phase LLM concurrency

> Product Requirement Prompt for **BreviaBook**. Operational throughput follow-up after
> `feat--checkpoint-remaining-phases.md`. Source of truth:
> [docs/ROADMAP.md](../docs/ROADMAP.md) §5, §7.1–§7.5, §11, §14. Operating rules:
> [CLAUDE.md](../CLAUDE.md).

## Goal

Make independent paid LLM work within each pipeline phase run concurrently, bounded by a
user-facing `--concurrency N` limit (default **4**). The pipeline remains phase-ordered:
`condense → synthesize → translate → vision rank`; only work *inside* one of those phases
may overlap. The output IR, checkpoint semantics, usage accounting, and visible progress must
remain correct and deterministic regardless of completion order.

## Why

- The complete pipeline is already asynchronous, but its four paid work loops await one item at
  a time: chunks in `Condenser.condense`, chapter groups in `Synthesizer.synthesize`, chapters
  in `Translator.translate_document`, and images in `VisionRanker.rank`.
- A 200–400 page book produces enough independent LLM calls that bounded parallelism materially
  improves wall-clock throughput for paid APIs. Existing `KeyPool` + `with_key_rotation` already
  handle per-call auth/rate-limit failover under this higher request rate.
- Concurrency must not weaken the recent checkpoint/fingerprint work: `--resume` must still
  reuse exactly matching successful results and retry failures.

## Scope

**In scope:**

- Add `--concurrency N` to both `breviabook condense` and `breviabook translate`; default is 4
  and CLI validation rejects values below 1.
- Thread the resolved value through `condense_book` to each paid phase.
- Use one `asyncio.Semaphore(concurrency)` plus `asyncio.gather` per phase, keeping the phase
  boundaries and existing prompts/retry loops intact.
- Preserve input order in all returned IR/results even when calls finish out of order.
- Make `CheckpointManager` writes safe for concurrent phase workers while retaining immediate
  append-and-flush durability.
- Prove correct shared usage accumulation and reporter progress under concurrent calls with
  deterministic test providers/reporters.

**Out of scope:**

- Parallelizing parsing, chunking, image selection, rendering, or crossing phase boundaries.
- Changing prompts, chunk sizes, target-ratio/trim behavior, checkpoint fingerprints or key
  namespacing.
- New provider retry/backoff behavior (including the separate Ollama retry debt).
- Distributed workers, multiprocessing, provider-specific rate-limit policy, or a queue that
  changes the user-selected concurrency dynamically.

## Non-negotiable constraints

- [ ] Clean-room only; no copied or translated reference-repository code.
- [ ] No new runtime dependency; `asyncio` and `threading` are stdlib.
- [ ] Preserve the IR invariants: code and image blocks are never summarized, translated, split,
      reordered, or otherwise mutated outside their established phase-specific behavior; tables
      remain unsplit and structural.
- [ ] Run parallel work only *within* its current phase. Synthesis never starts before every
      required condense result is available; translation/vision retain their present phase order.
- [ ] The result lists and rebuilt documents are in input order, not completion order.
- [ ] A successful worker checkpoint is appended and flushed before that worker reports progress;
      records remain fingerprint-validated on resume. Failed condense/synthesis/translation/
      unparsed-vision results keep their existing non-cacheable rules.
- [ ] `--resume` with the same inputs/configuration makes no duplicate successful LLM calls.
- [ ] `Usage.add` and `ProgressReporter.advance` remain correct with calls completing in any
      order; tests must not use a real LLM or network.
- [ ] No secrets, checkpoint files, or job databases are committed; no GPL/AGPL dependency is
      introduced (`ebooklib` and `PyMuPDF`/`fitz` remain forbidden).

## Audited concurrency design

### Work units and deterministic assembly

`asyncio.gather` returns results in the order of the awaitables supplied, not completion order.
Each phase should create work in source order, run each unit under its own shared semaphore, and
assemble from the ordered `gather` result:

| Phase | Existing sequential unit | Concurrent unit | Ordered result |
| --- | --- | --- | --- |
| Condense | `Chunk` | one `condense_chunk` / cached chunk | `list[CondensedChunk]` in `chunks` order |
| Synthesize | contiguous `chapter_index` group | one `synthesize_chapter` / cached chapter | `list[SynthesizedChapter]` in first-seen chapter order |
| Translate | `Chapter` | one `translate_chapter` | `Document.chapters` in input order |
| Vision | referenced `ImageBlock` | one image verdict | rebuilt chapter blocks and assets in document order |

For vision, first build an ordered work plan containing each image block's chapter/block position,
asset, context and fingerprint. Gather verdicts, then perform the existing keep/drop/caption
rebuild in plan/document order. Do not let workers mutate chapter block lists as they finish.
The ranker should accept an optional progress callback and pipeline should set the `Rank images`
total to the exact number of rankable image blocks (not the current hard-coded `1`). Cached work
also advances once because it is a completed phase unit.

### Checkpoints, usage, reporter, and keys

`CheckpointManager.record` currently appends a JSONL line, flushes it, then updates its in-memory
map. Preserve that operation with **no lock**: it is synchronous and has no `await`, so no other
coroutine can interleave its append/flush/map update on BreviaBook's single asyncio event loop.
Document that invariant in `record()` itself, including the requirement to revisit it if threads or
an `await` are ever introduced. Workers may record after their awaited provider call; do **not**
defer all records until after `gather`, which would turn a mid-phase interruption into loss of every
completed unit. JSONL record order may follow completion order; checkpoint keys/fingerprints, not
line order, determine resume behavior.

Providers update the shared `Usage` only after their awaited response returns. `Usage.add` has no
`await`, so on the single event loop each complete `+=` update is non-interleaved and totals remain
correct. Likewise, reporter methods are synchronous; workers call `on_progress` after any
successful cache write, and its `advance` cannot be interrupted by another coroutine. Do not add
an async lock or change the public reporter/usage APIs merely to serialize these non-yielding
critical sections. Tests must demonstrate the totals/counts under deliberately out-of-order
completion.

`KeyPool.current`/`rotate` and `with_key_rotation` are also synchronous around their state
transitions; retain the existing shared pool and rotation policy. Do not allocate a pool per task,
as that would defeat cross-call key rotation.

## Context & references

```yaml
- docs/ROADMAP.md                         # §5 pipeline order; §7.1/7.3/7.5 invariants; §11 tests; §14 licensing
- CLAUDE.md                               # mandatory stack, validation, clean-room rules
- breviabook/pipeline.py                  # condense_book wiring, phase totals, reporter callbacks
- breviabook/cli.py                       # both public commands and their asyncio.run calls
- breviabook/condense/condenser.py        # chunk cache/retry/record loop and CondensedChunk order
- breviabook/condense/synthesizer.py      # groupby chapter loop, cache/retry/trim behavior
- breviabook/translate/translator.py      # ordered chapter loop and intra-chapter batch resilience
- breviabook/images/vision_ranker.py      # image context, cache fingerprint, document rebuild
- breviabook/persistence/checkpoint.py    # append-only JSONL durability and torn-line tolerance
- breviabook/llm/usage.py                 # synchronous shared Usage.add accounting
- breviabook/llm/key_pool.py              # shared round-robin pool
- breviabook/llm/rate_limit.py            # shared pool rotation/backoff wrapper
- breviabook/ui/progress.py               # synchronous ProgressReporter contract
- tests/test_condenser.py                 # condenser checkpoint/failure patterns
- tests/test_synthesizer.py               # synthesis structure and checkpoint tests
- tests/test_translator.py                # bounded batches and checkpoint tests
- tests/test_vision_ranker.py             # vision checkpoint/rebuild tests
- tests/test_pipeline.py                  # end-to-end resume/routing-provider patterns
- tests/test_cli.py                       # Typer command option tests
```

Study patterns only; do not copy code from TranslateBooksWithLLMs, ollama-ebook-summary, or
OllamaBook-Summarize.

## Implementation blueprint

1. Define `DEFAULT_CONCURRENCY = 4` in `breviabook/config.py` as the sole default, which avoids
   stage-to-pipeline imports. Import that constant in `pipeline.py`, `cli.py`, and the four stage
   modules; use it for every public default. `condense_book` must reject `concurrency < 1` for
   programmatic callers; phase entry points should reject an invalid value before constructing a
   semaphore.
2. Add a typed `--concurrency` option (minimum 1, clear help text) to both CLI commands, defaulted
   from `config.DEFAULT_CONCURRENCY`. Pass it to `condense_book`; do not change dry-run behavior.
3. Add `concurrency` to `condense_book` and forward it to condenser, synthesizer, translator
   (both translate-only and translate-after-condense paths), and vision ranker. Retain the exact
   phase sequencing in `pipeline.py`.
4. Refactor `Condenser.condense` to prepare cached/missing chunk work in source order, run missing
   calls behind one semaphore, immediately record only successful uncached chunks, advance once
   per finished/reused chunk, then return gather results in chunk order. Preserve its retries,
   fingerprints, warning fields, and `reused_chunks` meaning.
5. Apply the same ordered gather pattern to `Synthesizer.synthesize`, with each chapter's complete
   contiguous group as one unit. Keep all trim retries for one chapter within that unit; never
   parallelize trim passes that depend on the preceding pass. Retain `synthesis_failed`, cache
   eligibility, fingerprints, and `reused_chapters` semantics.
6. Apply the pattern to `Translator.translate_document` at the chapter level. Do not parallelize
   the recursive/bisecting batches inside `_translate_batched`: their checkpoint keys, partial
   fallback accounting, and recursion remain sequential *within a chapter*. Return chapters in
   source order and invoke progress once per chapter.
7. Refactor `VisionRanker.rank` around its ordered image work plan, semaphore-protected verdict
   workers, per-item checkpoint/progress callback, and ordered rebuild. Preserve parse-failure
   keep-but-do-not-cache behavior, captions, and final pruning of unreferenced assets.
8. Update `CheckpointManager.record()`'s docstring with its single-event-loop, synchronous,
   non-yielding atomicity invariant; state that JSONL line ordering is not semantic while each
   record remains append+flush durable. Add no lock.
9. Add focused deterministic tests. Use delayed fake providers (for example, delays reversed by
   source index) and counting reporters; do not weaken existing resume/fingerprint tests.

### New / changed files

- `breviabook/cli.py` — `--concurrency` on condense and translate; forward to pipeline.
- `breviabook/config.py` — sole `DEFAULT_CONCURRENCY` declaration.
- `breviabook/pipeline.py` — default/validation and phase wiring; exact vision progress total.
- `breviabook/condense/condenser.py` — bounded ordered chunk concurrency.
- `breviabook/condense/synthesizer.py` — bounded ordered chapter concurrency.
- `breviabook/translate/translator.py` — bounded ordered document-chapter concurrency.
- `breviabook/images/vision_ranker.py` — bounded ordered image-verdict concurrency and progress.
- `breviabook/persistence/checkpoint.py` — single-event-loop append/flush/map invariant and
  contract documentation (no lock).
- `tests/test_cli.py`, `tests/test_checkpoint.py`, `tests/test_condenser.py`,
  `tests/test_synthesizer.py`, `tests/test_translator.py`, `tests/test_vision_ranker.py`,
  `tests/test_pipeline.py`, `tests/test_concurrency.py` — CLI validation, concurrency
  limit/order, resume, progress, usage, and checkpoint durability coverage.

## Validation gates

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict breviabook
uv run pytest -q
uv run pip-licenses --partial-match --fail-on "General Public License;GPL" --ignore-packages pyphen
```

### Feature-specific checks

- [ ] `breviabook condense --help` and `breviabook translate --help` expose `--concurrency`; `0`
      and negative values are rejected, and omitted value resolves to 4.
- [ ] For every phase, a delayed deterministic provider proves at most `N` requests are active
      and completion order can differ from input order.
- [ ] Condense, synthesize, translate, and vision outputs remain in their respective input/
      document order despite reversed completion order; code/table/image invariants remain true.
- [ ] A successful concurrent phase writes one valid JSONL record per eligible unit. Reloading a
      checkpoint and resuming makes zero calls for matching successful units; failed/non-cacheable
      units are retried exactly as before.
- [ ] Checkpoint records are append+flush durable per completion, not batch-written only after
      `gather`; concurrent record tests reload all records without corrupt JSONL.
- [ ] A shared usage-bearing delayed provider produces exactly the expected calls, prompt tokens,
      completion tokens, cached tokens, and cost after concurrent work.
- [ ] A counting `ProgressReporter` observes exactly one `advance` per phase unit (including
      cached units), with vision's total equal to its number of rankable image blocks.
- [ ] Parallel rate-limit/key-rotation tests preserve existing `KeyPool` behavior; no task owns a
      private pool and no real provider is contacted.
- [ ] Existing fingerprint invalidation matrix (model, ratio, language, glossary, image bytes)
      remains green; concurrency does not change cache validity.

## Acceptance criteria

- [ ] Users can set `--concurrency N` (default 4) for both CLI workflows, and the same bound is
      enforced in every paid phase.
- [ ] The pipeline gains throughput through intra-phase parallelism without any cross-phase
      overlap, output reordering, IR invariant break, unsafe checkpoint write, usage loss, or
      incorrect progress.
- [ ] Same-config `--resume` remains checkpoint/fingerprint-safe under concurrent execution.
- [ ] All five validation gates pass before the single implementation commit.

## Confidence score

8/10 — The independent work units and checkpoint boundaries are already explicit. The main care
is the vision ranker, which currently rebuilds blocks inline and therefore needs a two-stage
plan/gather/rebuild structure to keep document order and per-image progress exact.
