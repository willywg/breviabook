# Changelog

All notable changes to BreviaBook are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project adheres to
[Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-06-25

First public release. Condenses large technical ebooks (EPUB/PDF) to ~25–50% while
preserving code, tables, and meaningful figures, with optional same-pass translation.

### Added
- Format-agnostic IR (`Document → Chapter → Block`) with EPUB and PDF parsers (no
  GPL/AGPL deps: own EPUB builder, `pdfplumber`/`pypdf`, never `ebooklib`/PyMuPDF).
- Hierarchical condensation (per-chunk condense + per-chapter synthesis with length
  control); **code blocks are never summarized or split**.
- Integrated translation of the condensed book, with optional glossary; runs in resilient
  batches that retry and fall back to source text instead of crashing.
- Image preservation (Strategy A) plus optional vision ranking (`--rank-images`).
- Outputs: EPUB (own builder), PDF (weasyprint, optional `[pdf]` extra), Markdown.
- Multi-provider LLM via litellm: Ollama, OpenAI, Gemini, OpenRouter, and any
  OpenAI-compatible endpoint; key rotation and failover.
- Live TUI: banner, per-phase progress bars, and a real-time token/cost usage panel.
- `--reasoning-effort` control; thinking is **disabled by default** for Gemini (rewriting
  tasks gain nothing from it and it costs ~3.6× more).
- `--dry-run` token/page/compression/cost estimate; per-run usage report; compression and
  approximate page counts; `--resume` from a JSONL checkpoint.

[0.1.0]: https://github.com/willywg/breviabook/releases/tag/v0.1.0
