# Changelog

All notable changes to BreviaBook are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.5.0] — 2026-08-02

Condensation reaches the ratio it is asked for, and the dry run tells the truth
about what that will cost. Both were measured on full runs of a 479-page book
rather than reasoned about.

### Added
- **`breviabook.condense.cost_model` — the pass-structure cost model.** `--dry-run`
  predicted one clean pass per stage and came in at **0.44x–0.53x of the real bill**,
  always low, which is the wrong direction to be wrong in when the number is shown as
  money. Four things were missing, all of them measurable rather than guessable:
  synthesis re-runs whole chapters when the first pass overshoots; the condense prompt
  carries a structure contract, so its per-call overhead is ~900 tokens rather than the
  250 inherited from the translate command; the model returns JSON containing prose, not
  prose, which is a **1.53x envelope on the completion side** that Gemini prices at 5x
  input; and condensation overshoots its target, so every downstream pass reads more
  than the ratio implies.

  Same book, 51 chapters, 30% target, `gemini-3.6-flash`:

  | | dry run before | dry run after | actual |
  |---|---|---|---|
  | run 1 | $1.44 | **$3.02** | $3.27 |
  | run 2 | $1.44 | **$3.02** | $2.72 |

  One formula, no per-run tuning, landing at 0.92x and 1.11x — and slightly high rather
  than low. `estimate_condense` now calls it, so `--dry-run` and any embedder share a
  single formula instead of two that drift. `ESTIMATE_SPREAD` is exported for callers
  that should quote a range: these constants come from one book, and a point estimate to
  four decimals claims a precision the measurement does not have.

  `tests/test_cost_model.py` pins the prompt-overhead constants by building a real prompt
  and counting it, so editing a prompt fails a test instead of quietly skewing a price.

- `degraded_runs` on `CondensedChunk` and `SynthesizedChapter`, surfaced as a single aggregate
  pipeline warning rather than one line per run.

### Changed
- **Length is now steered where steering works, and the trim loop stops paying for nothing.**
  Measured across eight chapters: a synthesis pass's output tracks the text it is given
  (0.93x–1.00x) rather than the word count it is told, so every pass shaved the same ~6% at the
  price of regenerating a whole chapter — and one pass returned the identical token count, after
  which the loop bought a second on the same premise. Three changes follow from that:
  the condense prompt now asks for `CONDENSE_ASK_FACTOR` (0.85) of the target, because the model
  condenses to ~1.2x whatever ratio it is asked and the per-chunk prompt is the one place the
  ratio actually steers the result; the trim loop stops when a pass comes back no shorter, and
  discards a pass that came back longer; and `max_trim_passes` defaults to 1 rather than 2.

  Full book, 51 chapters, 30% target, `gemini-3.6-flash`:

  | | before | after |
  |---|---|---|
  | cost | $3.27 | **$2.72** |
  | LLM calls | 220 | 198 |
  | completion tokens | 310,154 | 250,467 |
  | output ratio | 34.2% | 33.9% |
  | chapters entering synthesis inside the trim threshold | 16/51 | **26/51** |

  The same output for 17% less: what was removed was waste, not text. The output ratio barely
  moves because the trim passes that are gone were buying real length — just at roughly the
  dearest price in the pipeline.

- **Condensed blocks now say where they came from.** Every entry in a `[TEXT n]` array carries
  `"block": k`, naming its source block, instead of being matched back by position. Position
  could not express "these two paragraphs became one" without also losing which entry used to
  be the list, so mixed runs had to choose between condensing and keeping their structure —
  and the parser rejected whichever one the model gave up. An explicit address expresses both:
  paragraphs merge and split freely, while a list or a quote must still be accounted for
  exactly once and keeps its own type. The positional form is still read, so a model that
  answers the old way loses nothing.

  Measured on the same eight mixed-run chunks of *Site Reliability Engineering*, both passes,
  against `gemini-3.6-flash`:

  | | before | after |
  |---|---|---|
  | runs kept at source wording (synthesis) | 20 | 1 |
  | arrays returned without an address | 61 | 0 |
  | end-to-end output ratio (30% target) | 37.4% | 30.5% |
  | cost | $0.248 | $0.182 |

  Cheaper as well as closer: a run that parses is a run that gets trimmed, so the length-control
  loop stops burning passes on chapters it cannot move.

  The checkpoint fingerprint moves to `condense_block_format:3`, so a `--resume` across this
  version recomputes rather than mixing answers to two different contracts.

### Fixed
- **Condensation no longer rejects itself for having condensed.** The prompt asks the model to
  smooth and shorten prose, and the parser then demanded one output block per source block —
  but merging paragraphs is what shortening *is*. A run of nothing but paragraphs now accepts
  any number of blocks back, exactly as the plain-string form of the same response already did.
  A list or a quote still needs its alignment, because those carry a type worth preserving.
- **A run the model shapes wrongly no longer costs the whole chapter.** Validation was
  all-or-nothing: one bad run out of forty-eight discarded every good one beside it, and the
  retry sent the identical prompt, so a deterministic disagreement failed all three attempts.
  Each run now falls back to its own source wording and the rest of the pass is kept. Measured
  on a real book, chapters that failed 3/3 attempts now succeed on the first, with roughly a
  fifth of runs keeping their original text instead of the whole chapter doing so.
- **Retries are spent where they can help.** A pass is retried when the reply is not JSON, or
  when no run at all could be read — not when one run came back mis-shaped. Failed chapters
  previously paid for three full-chapter calls that could never have succeeded.
- **A run the model omits entirely keeps its text.** A missing key was indistinguishable from
  "condensed to nothing" and silently deleted the passage. An explicit empty value still means
  nothing survives.
- **Chapter-level failures skipped length control**, which is the only stage that enforces
  `target_ratio` — so a JSON shape disagreement quietly became a book at ~57% of its input when
  30% was asked for. With the above, the trim loop runs.

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

[0.5.0]: https://github.com/willywg/breviabook/releases/tag/v0.5.0
[0.4.0]: https://github.com/willywg/breviabook/releases/tag/v0.4.0
[0.3.0]: https://github.com/willywg/breviabook/releases/tag/v0.3.0
[0.2.0]: https://github.com/willywg/breviabook/releases/tag/v0.2.0
[0.1.0]: https://github.com/willywg/breviabook/releases/tag/v0.1.0
