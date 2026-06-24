# CLAUDE.md — Brevia

Guidance for AI agents working in this repository. The complete specification and
phased build plan live in **[docs/ROADMAP.md](docs/ROADMAP.md)** — that document is
the source of truth; this file is the quick operating manual.

## What Brevia is

A CLI that takes a large technical ebook (EPUB/PDF, 200–400 pages) and produces a
**condensed version** (~25–50% of the original) that preserves **code, formulas, and
essential images/diagrams**, optionally **translated** in the same pass. Outputs
EPUB, PDF, and Markdown. Multi-provider LLM (Ollama, OpenAI, Gemini, OpenRouter, and
any OpenAI-compatible endpoint).

## Core architectural principle (read first)

Everything flows through a **format-agnostic Intermediate Representation (IR)** —
`Document → Chapter → Block` (pydantic). Parsers produce IR, the condenser/translator
transform IR text blocks, renderers consume IR. **`code` and `image` blocks are never
summarized or split.** Adding a format = one parser or one renderer, nothing else.
See ROADMAP §3 and §6 for the model and folder layout.

Pipeline: `parse → chunk → condense → synthesize → (translate) → image-select → render`,
with checkpoint/resume between chunking and translation. See ROADMAP §5.

## License rules — NON-NEGOTIABLE (ROADMAP §14)

- Project license: **Apache-2.0**.
- **Clean-room only.** The reference repos (TranslateBooksWithLLMs, ollama-ebook-summary,
  OllamaBook-Summarize) are **study material, never copy material**. Never copy/paste or
  line-by-line translate their code. Keep them OUTSIDE this tree (`~/projects/open-source/`),
  never vendor or submodule them.
- **No AGPL/GPL runtime dependencies — ever.** Forbidden: `ebooklib`, `PyMuPDF`/`fitz`.
  Use the permissive substitutes below. CI enforces this with `pip-licenses --fail-on "GPL"`.
- **Never commit** API keys or job databases (`.env`, `*.sqlite` are gitignored).

## Tech stack (permissive only)

- Python **3.11+**, package manager **`uv`**.
- CLI: `typer` + `rich`. Config: `pydantic` v2 + `python-dotenv`.
- EPUB: our own builder with `zipfile` (stdlib) + `lxml`/`beautifulsoup4` — **not ebooklib**.
- PDF read: `pdfplumber` + `pypdf` — **not PyMuPDF**. PDF write: `weasyprint`.
- Tokens: `tiktoken` (char-based fallback). LLM: `litellm` (preferred) and/or `httpx`.
- NER glossary (late phase): `spacy`.

## Quality gates (must pass from Phase 0)

```bash
uv run ruff check .            # lint (rules: E,F,I,UP,B,SIM)
uv run ruff format --check .   # format
uv run mypy --strict brevia    # types — strict mode, type the whole pipeline
uv run pytest                  # tests (pytest + pytest-asyncio)
uv run pip-licenses --fail-on "GPL"   # license audit — blocks any GPL/AGPL dep
```

`ruff` and `mypy` must pass clean from the first commit. Use a deterministic **mock
provider** for pipeline tests; don't call a real LLM in tests.

## Workflow

This project is driven by the **PRP (Product Requirement Prompt) workflow**. Each phase
in ROADMAP §10 (Phase 0 → Phase 12) becomes one PRP under `PRPs/`. Build phases **in
order**; each must be functional and tested before the next. **MVP = Phases 0–7.**

## Things NOT to do (ROADMAP §12)

- Don't copy TBL's 1:1 placeholder reinjection — we build a fresh EPUB from scratch.
- Don't use ~450-token chunks; summarization needs ~2000-token chunks.
- Never split a `code` block or a table across chunks.
- Validate entry paths when unpacking EPUBs (zip-slip). Don't forward provider keys to an
  arbitrary `--api-endpoint` (SSRF). See `brevia/utils/security.py`.
- "Output longer than input" is a red flag — detect and warn.
