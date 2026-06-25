# PRP: CLI pipeline wiring (MVP capstone)

> Product Requirement Prompt for **BreviaBook**. Source of truth: [docs/ROADMAP.md](../docs/ROADMAP.md) §5, §8, §13.
> Operating rules: [CLAUDE.md](../CLAUDE.md). Connects Phases 1–7 end-to-end so the MVP is runnable.

## Goal

Wire the full pipeline behind `breviabook condense`: parse → chunk → condense → synthesize →
select images → render (EPUB/PDF/MD), with `--resume`, `--dry-run`, `--formats`, and progress
output. Today the components exist but the CLI is a stub.

## Why

- Satisfies v1 acceptance §13.1 ("produces all three files"), §13.4 (`--resume`), §13.5 (`--dry-run`).
- Makes the MVP actually usable on a real book.

## Scope

**In scope:**
- `breviabook/pipeline.py`: UI-agnostic orchestrator — `condense_book(...) -> CondenseResult` and
  `estimate_condense(...) -> Estimate`. Provider is **injected** (testable with the mock).
- Rewire `breviabook/cli.py condense` to build the provider via the factory and call the pipeline;
  `--dry-run` prints the estimate; render each requested format; surface warnings
  (output>input) and resume status with `rich`.
- Tests with the mock provider (formats md+epub to avoid weasyprint system libs).

**Out of scope:** PDF *input* (Phase 8 — `.pdf` raises a clear "coming in Phase 8"),
translation execution (Phase 10 — `--translate-to` warns), cost numbers in `--dry-run`
(Phase 12 — report tokens only).

## Non-negotiable constraints (CLAUDE.md)

- [ ] Code/images preserved through the whole pipeline (already guaranteed by components).
- [ ] `--resume` must not reprocess condensed chunks (checkpoint); a fresh run clears stale state.
- [ ] `--dry-run` performs NO LLM call.
- [ ] Pipeline is import-safe without weasyprint libs (PDF renderer already lazy-imports).
- [ ] Provider injected into the pipeline (no hidden global) so it is unit-testable.

## Context & references

```yaml
- breviabook/parsers/epub_parser.py       # EpubParser
- breviabook/condense/chunker.py          # Chunker, count_document_tokens
- breviabook/condense/condenser.py        # Condenser, assemble_condensed_document
- breviabook/condense/synthesizer.py      # Synthesizer, synthesized_to_document
- breviabook/images/selector.py           # ImageSelector
- breviabook/render/{md,epub,pdf}_renderer.py
- breviabook/persistence/checkpoint.py    # CheckpointManager
- breviabook/llm/factory.py               # get_provider
- breviabook/config.py                    # load_settings, defaults
```

## Design

- `condense_book(*, input_path, out_dir, formats, provider, model, target_ratio, chunk_tokens,
  resume, checkpoint_path=None, translate_to=None, log=noop) -> CondenseResult`:
  1. choose parser by suffix (`.epub` → EpubParser; `.pdf` → NotImplementedError Phase 8).
  2. parse → `input_tokens = count_document_tokens(doc)`.
  3. chunk → checkpoint at `out_dir/.breviabook/<stem>.jsonl`; `clear()` it unless `resume`.
  4. condense (checkpointed) → collect `output_longer_than_input` warnings.
  5. synthesize → `synthesized_to_document`.
  6. if `translate_to`: append a "translation lands in Phase 10" warning (no-op).
  7. `ImageSelector().select(...)`.
  8. render each format (stem `<input-stem>-condensed`) → output paths.
  9. return paths + token totals + warnings.
- `estimate_condense(input_path, chunk_tokens, target_ratio)`: parse + token/chunk/chapter
  counts + estimated output tokens. No LLM.
- CLI: `asyncio.run(condense_book(...))`; `rich` table of outputs; print warnings; resume note.

## Implementation blueprint

1. `breviabook/pipeline.py` — `CondenseResult`, `Estimate`, `condense_book`, `estimate_condense`,
   `_parser_for`, `_renderer_for`.
2. `breviabook/cli.py` — implement `condense` (build provider, dry-run branch, run pipeline, print).
3. Tests: `tests/test_pipeline.py` (mock provider) + a CLI smoke test via Typer's CliRunner.

### New / changed files

- `breviabook/pipeline.py` (new), `breviabook/cli.py` (rewire)
- `tests/test_pipeline.py` (new)

## Validation gates (must all pass)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict breviabook
uv run pytest -q
uv run pip-licenses --fail-on "GPL"
```

### Feature-specific checks

- [ ] `condense_book` on the fixture (mock provider, formats md+epub) writes both files; the
      EPUB re-parses.
- [ ] `--dry-run` returns token/chunk counts and makes no provider call.
- [ ] A `.pdf` input raises a clear Phase-8 message.
- [ ] `--translate-to` adds a Phase-10 warning but still produces output.
- [ ] Re-running with `resume=True` does not re-call the provider for done chunks; a fresh run
      (resume=False) clears the checkpoint.
- [ ] CLI `condense --dry-run` exits 0 and prints an estimate.

## Acceptance criteria

- [ ] `breviabook condense fixture.epub --formats md,epub --out DIR` produces both files end-to-end.
- [ ] `--resume` and `--dry-run` behave per §13.4/§13.5.
- [ ] All five validation gates green.

## Confidence score

8/10 — Straight integration of tested components; main care is checkpoint resume semantics and
keeping the pipeline import-safe + provider-injected.
