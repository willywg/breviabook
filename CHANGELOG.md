# Changelog

All notable changes to BreviaBook are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- `breviabook translate INPUT --to LANG` — translates the full book to the target language
  without condensing it, preserving code, tables, and images. Supports `--resume` with a
  translation-specific checkpoint (keyed by language and glossary so switching target languages
  or editing the glossary invalidates stale cache). `--dry-run` uses a translate-only cost
  model (target-language expansion, no condense/synthesize calls).
- Translation checkpoint: completed batches are persisted per chapter under
  `out_dir/.breviabook/{stem}-{lang}.jsonl`. An interrupted `--resume` reuses them and only
  pays for the remaining batches. The hash guard includes target language and glossary content
  to prevent silent corruption when re-running with different settings.
- `untranslated_units` warning surfaced at the end of both `condense` and `translate` runs.

### Changed
- `condense` and `translate` now share ~50 lines of CLI plumbing (validation helpers, report
  tables) extracted from `cli.py` — no copy-paste.

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
