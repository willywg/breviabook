# Changelog

All notable changes to BreviaBook are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.4.0] — 2026-07-19

Round-trip fidelity: covers, in-book links, and block-level styling now survive the
rebuilt EPUB/PDF. Validated end-to-end on a real book (EN→ES translation pass).

### Added
- **In-book cross-references survive the rebuild.** TOC entries and chapter links are remapped to
  their new output locations (opaque `bbref:` anchors resolved at render), so a translated or
  condensed EPUB keeps working internal navigation and the link styling — or cleanly unwraps a
  link whose target was dropped. External `http(s)`/`mailto` links are unchanged; the sanitizer
  allowlist is not widened (internal links use a dedicated opaque scheme).
- **Book cover is preserved.** The source cover image round-trips into the output EPUB as a
  proper `cover-image` (manifest property + legacy `<meta name="cover">` + a `cover.xhtml` at the
  spine head), so readers show the cover thumbnail again.
- **Block-level styling round-trips.** Class-driven bold/italic on paragraphs and headings (e.g.
  `font-weight:bold` sub-headings like "Notice of Rights") now render, and images centered by
  their wrapper are centered in the output — not just their captions.
- **Intra-paragraph line breaks (`<br>`) are preserved**, so credit/address blocks keep their
  line structure instead of collapsing into one run of prose. Markdown emits GFM hard breaks.
- **Translated output sets the target language.** A Spanish translation now reports
  `dc:language = es` (EPUB) / `lang="es"` (PDF) instead of the source language.

### Fixed
- Markdown no longer leaks internal `bbref:` link placeholders (nested inside `<sup>`/`<span>`).
- The `--concurrency` CLI help test no longer fails under CI's colored, narrow terminal (ANSI
  codes split the option name); CI is green again.

## [0.3.0] — 2026-07-17

Fidelity, resilience, and speed.

### Added
- `--concurrency N` — bounded parallel LLM calls within each phase (condense, synthesize,
  translate, vision). Order is always preserved; the default keeps runs gentle, and raising it
  speeds up providers that allow concurrent requests.
- Retry with exponential backoff on the Ollama provider for transient failures (connection
  errors / timeouts while the local server is busy or loading a model) — a single hiccup no
  longer aborts the whole run.
- **Block-level presentation is preserved**: text alignment (centered / right) on headings,
  paragraphs, and quotes, plus list marker style and color, now round-trip from the source EPUB
  through to the output EPUB and PDF. Markdown degrades cleanly.

### Fixed
- **Lists and block quotes survive condensation** as real `ListBlock` / `QuoteBlock` instead of
  being flattened into paragraphs. A structured block that the model returns as plain prose is
  kept verbatim rather than silently losing its structure.
- `--resume` is far more reliable: condense, synthesis, translate-after-condense, and vision
  checkpoints are now fingerprinted, so changing the model, target ratio, chunk size, or the book
  itself recomputes stale records instead of silently reusing them.
- The CI license audit now matches copyleft license strings correctly (e.g. "GNU AFFERO
  GPL 3.0"), closing a gap where a GPL/AGPL dependency could have slipped past the exact-match gate.

### Security
- The `--api-endpoint` SSRF guard is now wired end-to-end: provider API keys are never forwarded
  to disallowed or local/internal endpoints.

## [0.2.0] — 2026-07-16

Full-book translation and preserved inline styling.

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
- **Inline formatting is preserved** through parse → translate → render: bold, italic, links,
  inline code, and **color** (both inline styles and CSS-class-resolved, e.g. colored headings).
  Text blocks gained a sanitized `rich` field; a strict allowlist keeps untrusted EPUB markup
  safe. `text` stays the plain projection, so condensation is unchanged.
- **Inline images** embedded mid-text (e.g. a small icon inside a heading) are kept and rendered
  in place, not dropped.
- `untranslated_units` warning surfaced at the end of both `condense` and `translate` runs.

### Changed
- Translation sends the styled form and instructs the model to keep tags; the reply is
  re-sanitized and its tag signature verified. On divergence the translated text is kept and
  only that segment's styling is dropped — never misattributed markup.
- On a persistent malformed-JSON reply, a batch is **bisected** and each half retried, so one
  problematic segment no longer sinks its ~40 neighbours into the source language.
- Generic image captions (`alt="Image"`, "Figure", …) are dropped instead of rendered as text.
- `condense` and `translate` share CLI plumbing (validation helpers, report tables) — no copy-paste.

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

[0.4.0]: https://github.com/willywg/breviabook/releases/tag/v0.4.0
[0.3.0]: https://github.com/willywg/breviabook/releases/tag/v0.3.0
[0.2.0]: https://github.com/willywg/breviabook/releases/tag/v0.2.0
[0.1.0]: https://github.com/willywg/breviabook/releases/tag/v0.1.0
