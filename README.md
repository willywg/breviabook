# BreviaBook

> Condense large technical ebooks (EPUB/PDF) into a fast, filler-free version — **preserving
> code, formulas, and the important diagrams** — and optionally **translate** them in the same
> pass. Outputs **EPUB, PDF, and Markdown**.

Multi-provider LLM from day one: **Ollama** (local), **OpenAI**, **Gemini**, **OpenRouter**,
and any **OpenAI-compatible** endpoint (vLLM, LM Studio, LocalAI). Runs fully local on a laptop
with Ollama, or via a paid API when it makes sense.

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

## Why

Technical books are long and padded. BreviaBook reads dense books fast without losing the parts
that matter — **code examples, tables, and meaningful figures survive; filler doesn't** — and
can deliver the result in your language (e.g. English → Spanish) in one go.

## Features

- **Structure-aware condensation** — chapter-aware chunking; **code blocks are never
  summarized or split**.
- **Hierarchical summarization** — per-chunk condense + per-chapter synthesis with active
  length control toward a `--target-ratio`.
- **Image preservation (the differentiator)** — keeps images whose section survives
  (Strategy A); optional **vision ranking** (`--rank-images`) drops decorative images and
  improves captions.
- **Integrated translation** — translates the *already-condensed* book (much cheaper) with an
  optional glossary for consistent terminology; code stays untranslated. Runs in resilient
  batches: a malformed model response retries and falls back to the source text instead of
  crashing the run.
- **Three outputs** — EPUB (our own builder), PDF (weasyprint), Markdown.
- **Live TUI** — a banner plus per-phase progress bars (parse → condense → synthesize →
  translate → render) and a usage panel that ticks token/cost totals in real time. Degrades to
  plain text when output is piped.
- **Cost control for reasoning models** — `--reasoning-effort disable` turns off "thinking" on
  models like `gemini-3-flash-preview`, which otherwise spend most output tokens on discarded
  reasoning (~3.6× cheaper on a real run, same quality). See [Cost & reasoning models](#cost--reasoning-models).
- **Compression report** — every run prints how much smaller the result is and an approximate
  page count (e.g. `~479 → ~149 pages, 69% smaller`).
- **Resumable** — `--resume` continues an interrupted job from a checkpoint (already-condensed
  chunks are reused, not re-billed).
- **Dry-run + cost** — `--dry-run` estimates tokens, pages, compression, and approximate cost
  without calling the LLM.
- **Usage report** — every run prints prompt/completion/cached tokens and estimated cost.

## Install

Requires **Python 3.11+** and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/willywg/breviabook.git
cd breviabook
uv sync                # EPUB + Markdown — no system libraries needed
uv sync --extra pdf    # add PDF output (needs the system libs below)
```

Copy `.env.example` to `.env` and set what you need (defaults use local Ollama):

```bash
cp .env.example .env
```

> **Run without cloning (uvx):** BreviaBook is a CLI, so it also runs via `uvx` straight from git.
> EPUB + Markdown need nothing extra:
> `uvx --from "git+https://github.com/willywg/breviabook" breviabook condense book.epub --formats epub,md`
> — add PDF with `"breviabook[pdf] @ git+…"`. Once published, this becomes `uvx breviabook`.

### PDF output requirements

PDF rendering uses [weasyprint](https://weasyprint.org/), which needs system libraries
(EPUB and Markdown need nothing extra):

- **macOS:** `brew install pango gdk-pixbuf libffi` — on Apple Silicon also
  `export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`.
- **Debian/Ubuntu:** `libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libffi-dev`.

## Quickstart

```bash
# Local, all three formats, with Ollama
uv run breviabook condense book.epub --formats epub,pdf,md --out ./out/

# Estimate tokens + cost first, without calling the LLM
uv run breviabook condense book.epub --dry-run

# Condense + translate to Spanish with a cloud model (Gemini thinking is off by default)
uv run breviabook condense book.epub \
  --provider gemini --model gemini-3-flash-preview \
  --translate-to Spanish --source-lang English --glossary glossary.json \
  --formats epub,md --out ./out/

# Drop decorative images with a vision model, and resume if interrupted
uv run breviabook condense book.pdf --provider gemini --model gemini-3-flash-preview \
  --rank-images --resume --out ./out/
```

## CLI

```
breviabook condense INPUT.{epub,pdf} [options]

  --provider        ollama | openai | gemini | openrouter        (default: ollama)
  --model           model tag (default from .env)
  --api-endpoint    base URL for OpenAI-compatible servers (vLLM/LM Studio/LocalAI)
  --target-ratio    target size, e.g. 0.30 = ~30% of the original
  --formats         comma list of epub,pdf,md                     (default: epub,pdf,md)
  --translate-to    target language (omit = no translation)
  --source-lang     source language (optional hint)
  --glossary        glossary JSON {source_term: target_term}
  --rank-images     use a vision model to score/drop images
  --reasoning-effort  disable | low | medium | high  (thinking budget for reasoning models;
                      "disable" is cheapest — recommended for gemini-3 condensation)
  --manual-toc      manual TOC JSON for PDFs without an outline
  --out             output directory                              (default: ./output)
  --resume          resume from checkpoint
  --dry-run         estimate tokens/cost/pages only, no LLM call
```

## Cost & reasoning models

Some cloud models "think" before answering. For condensation/translation — rewriting tasks,
not reasoning tasks — that thinking is pure waste: on a real run ~94% of output tokens were
discarded reasoning, billed as output. **BreviaBook therefore disables thinking by default** for
providers that have it on (Gemini). To restore a model's native thinking, pass
`--reasoning-effort auto` (or set `low`/`medium`/`high` explicitly).

| Run (Introducing Go, EPUB → Spanish) | Cost | Output tokens | Quality |
|---|---|---|---|
| thinking on (`--reasoning-effort auto`) | $0.78 | 243k | excellent |
| **default (thinking disabled)** | **$0.22** | 55k | **excellent (identical)** |

Estimate first with `--dry-run` (no LLM call). Note the dry-run assumes no reasoning tokens —
which matches the default; if you re-enable thinking with `--reasoning-effort auto`, the real
cost can be several times the estimate. Pricing for `gemini-3-flash-preview`: ~$0.50 / 1M
input, ~$3.00 / 1M output.

## Configuration (`.env`)

```
LLM_PROVIDER=ollama
OLLAMA_ENDPOINT=http://localhost:11434
DEFAULT_MODEL=gemma4:e4b

OPENAI_API_KEY=         # comma-separated for key rotation
GEMINI_API_KEY=
OPENROUTER_API_KEY=

DEFAULT_TARGET_RATIO=0.30
DEFAULT_CHUNK_TOKENS=2000
IMAGE_STRATEGY=keep_referenced   # keep_referenced | vision_ranked
```

## How it works

Everything flows through a format-agnostic **Intermediate Representation (IR)**:

```
parse → chunk → condense → synthesize → (translate) → image-select → render
        EPUB/PDF → IR        per-chunk    per-chapter    glossary      Strategy A/B   EPUB/PDF/MD
```

Parsers turn EPUB/PDF into the IR; the condenser and translator transform its text blocks
(leaving code and images intact); renderers emit the final files. Adding an input or output
format means writing one parser or one renderer — the condensation logic doesn't change.

See the full design and build plan in **[docs/ROADMAP.md](docs/ROADMAP.md)**.

## Development

```bash
uv run ruff check . && uv run ruff format --check .   # lint + format
uv run mypy --strict breviabook                           # types
uv run pytest -q                                       # tests
uv run pip-licenses --fail-on "GPL"                    # license audit (blocks GPL/AGPL)
```

## License

[Apache-2.0](LICENSE). BreviaBook is **inspired by** open-source work but contains no copied code
and depends on no copyleft (GPL/AGPL) libraries — see [docs/ROADMAP.md §14](docs/ROADMAP.md)
and [NOTICE](NOTICE).
