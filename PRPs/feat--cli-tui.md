# PRP feat — CLI TUI: banner + live progress & usage

## Goal
Replace the coarse, per-phase `· message` logging with an interactive terminal UI: a BreviaBook
ASCII banner on startup, a per-phase progress bar (Parse → Condense → Synthesize →
[Translate] → [Rank images] → Render), and a **live usage panel** that ticks token and cost
totals as the run proceeds. Degrade cleanly to plain text when output is piped/redirected.

## Why
Real-run UX gap surfaced during the first cloud demo (Introducing Go → Spanish, gemini): the
longest phase (condensing 25 chunks) printed one static line and went silent for minutes — no
`x/25`, no spinner, no sign the cost was climbing to ~$0.78. The pipeline already had the
hooks needed (`Condenser.on_progress`, `Synthesizer.on_progress`, and `provider.usage`
accumulating per call); they just weren't surfaced.

## Design
- `breviabook/ui/banner.py` — `banner_renderable()` / `print_banner(console)`; block-style wordmark
  in a cyan panel + tagline + version. Separated build/print so it is unit-testable.
- `breviabook/ui/progress.py` — a small `ProgressReporter` Protocol (`note` / `phase` / `advance`)
  with three implementations:
  - `RunReporter` — `rich.Live` group of a `Progress` (spinner→green ✓ per phase, bar, M/N, %)
    plus a usage `Panel` rebuilt from a `Usage` source (the provider's live-accumulating
    `provider.usage`). Phases added lazily as they start; `note()` prints above the live region.
  - `LogReporter` — forwards phases/notes to a log callback (non-TTY); per-item advances are
    silent to avoid spam in pipes.
  - `NullReporter` — silent no-op; the pipeline default (keeps tests unchanged).
- Pipeline: `condense_book(..., reporter=None)`. Falls back to `LogReporter(log)` when the old
  `log=` arg is passed, else `NullReporter`. Phase/advance calls wrap each stage; per-item
  `on_progress` lambdas drive `advance()`. Added `Translator.translate_document(on_progress=)`.
- CLI: prints the banner when `console.is_terminal`; picks `RunReporter` (TTY) vs `LogReporter`
  (pipe) and wraps the run in the reporter context, passing `provider.usage` as the live source.

## Files
- NEW `breviabook/ui/__init__.py`, `breviabook/ui/banner.py`, `breviabook/ui/progress.py`
- EDIT `breviabook/pipeline.py` (reporter param + phase/advance wiring)
- EDIT `breviabook/translate/translator.py` (`on_progress` hook)
- EDIT `breviabook/cli.py` (banner + reporter selection)
- NEW `tests/test_ui.py`

## Validation
```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict breviabook
uv run pytest -q          # 159 passed, 1 skipped
uv run pip-licenses --fail-on "GPL"
```

## Notes / gotchas
- `__exit__` must return `None` (not `bool`) under mypy strict (`exit-return`).
- No new dependency — `rich` was already in the stack.
- Live usage is read straight from `provider.usage`; no usage threading through callbacks.

## Confidence: 9/10
