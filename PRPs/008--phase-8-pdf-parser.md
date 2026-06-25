# PRP: Phase 8 — PDF parser + TOC inference

> Product Requirement Prompt for **BreviaBook**. Source of truth: [docs/ROADMAP.md](../docs/ROADMAP.md) §2.3, §4, §6, §10 (Phase 8), §14.
> Operating rules: [CLAUDE.md](../CLAUDE.md). Builds on PRP 001 (IR) and PRP 004 (LLM layer).

## Goal

Parse a PDF into the IR: `breviabook/parsers/pdf_parser.py` (pdfplumber for text/tables, pypdf for
images/metadata/outline) + `breviabook/parsers/toc_inference.py` (LLM infers chapters when the PDF
has no outline) + a `--manual-toc FILE` (JSON) fallback. Wire PDF input through the CLI/pipeline.

## Why

- Lets BreviaBook condense PDFs, not just EPUBs — the owner's books come in both formats.
- TOC inference + manual TOC handle the common case of PDFs without bookmarks.

## Scope

**In scope:**
- `pdf_parser.py`: `TocEntry`, `ExtractedPdf`, `PdfParser.extract/build/parse`. Heuristic
  block building from font/geometry: headings (bold / large font), code (monospace font),
  paragraphs (line-gap grouping), tables (pdfplumber, excluded from text by bbox), images
  (pypdf, **deduped by content hash**). Chapters from outline / manual TOC / inferred TOC.
- `toc_inference.py`: async `infer_toc(provider, model, page_texts, max_pages)` → `[TocEntry]`.
- `breviabook/utils/jsonx.py`: shared `extract_json_object` (ValueError on failure); refactor
  `condense/common.extract_json` onto it; toc_inference uses it too.
- CLI `--manual-toc` option; pipeline PDF path (outline → manual → LLM → single chapter).
- A committed `tests/fixtures/sample.pdf` (+ its weasyprint builder) and tests.

**Out of scope:** perfect layout reconstruction, multi-column PDFs, OCR of scanned PDFs.

## Non-negotiable constraints (CLAUDE.md / ROADMAP §14)

- [ ] No `PyMuPDF`/`fitz`. Only `pdfplumber` (MIT) + `pypdf` (BSD).
- [ ] No code block is fabricated/altered; monospace runs captured as `CodeBlock` text.
- [ ] Images deduped (the same XObject reported on several pages must embed once).
- [ ] LLM TOC inference is optional and resolved in the async layer; `PdfParser` stays sync.
- [ ] `--dry-run` path performs NO LLM call (use outline/manual/single only).

## Context & references (validated by exploration)

```yaml
- pypdf: PdfReader(path).metadata.title; .outline (+ get_destination_page_number); page.images
         -> ImageFile(.name,.data). NOTE: a shared image is reported on multiple pages -> dedupe.
- pdfplumber: page.extract_text_lines() (text/top/bottom/chars[fontname,size]);
              page.find_tables() (.bbox, .extract()); fonts seen: PT-Serif (body),
              PT-Serif-Bold (heading), Andale-Mono (code).
- docs/ROADMAP.md §2.3 (TOC inference, MAX_CHUNK_CHARS hints), §4 (libs), §10 Phase 8
```

## Design

- **extract(path):** metadata (pypdf); per-page blocks via line classification —
  code if font is monospace (group consecutive lines, preserve `\n`); heading if bold or
  size > body*factor; else paragraphs grouped by vertical gap. Exclude lines inside table
  bboxes; add `TableBlock` from `find_tables().extract()`; order blocks by vertical position.
  Per-page images via pypdf, hashed for global dedupe. Outline → `[TocEntry]`. Keep raw page
  texts for inference.
- **build(extracted, toc):** if `toc` given (outline/manual/inferred), assign page ranges to
  chapters (title from toc, blocks = pages' blocks, no synthetic heading — page text already
  carries it); else a single chapter. Embed each unique image once at first occurrence.
- **infer_toc:** first N page texts with `[PAGE k]` markers → JSON
  `{"chapters":[{"title","start_page"}]}`.

## Implementation blueprint

1. `utils/jsonx.py` — `extract_json_object`; refactor `condense/common.extract_json`.
2. `parsers/pdf_parser.py` — extraction + heuristics + build.
3. `parsers/toc_inference.py` — `infer_toc` + prompt.
4. `cli.py` `--manual-toc`; `pipeline.py` PDF path (extract → choose toc → build); both
   `condense_book` and `estimate_condense`.
5. Tests: `tests/fixtures/_build_sample_pdf.py` (+ committed `sample.pdf`),
   `tests/test_pdf_parser.py`, `tests/test_toc_inference.py`, pipeline PDF test.

### New / changed files

- `breviabook/utils/jsonx.py`, `breviabook/parsers/pdf_parser.py`, `breviabook/parsers/toc_inference.py`
- `breviabook/condense/common.py`, `breviabook/cli.py`, `breviabook/pipeline.py`, `pyproject.toml` (mypy)
- `tests/fixtures/_build_sample_pdf.py` + `tests/fixtures/sample.pdf`, tests

## Validation gates (must all pass)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict breviabook
uv run pytest -q
uv run pip-licenses --fail-on "GPL"
```

### Feature-specific checks

- [ ] Parsing `sample.pdf` yields 2 chapters from the outline with the right titles.
- [ ] The monospace `def hello()` lines come through as a `CodeBlock`; paragraphs are merged
      from wrapped lines; the table is a `TableBlock` (not duplicated as paragraph text).
- [ ] The embedded PNG is extracted exactly once (deduped) despite appearing on both pages.
- [ ] `infer_toc` (mock provider) returns chapters; invalid JSON raises cleanly.
- [ ] `--manual-toc` JSON overrides outline; a PDF without outline + no provider yields one
      chapter; pipeline condenses `sample.pdf` end-to-end (mock provider, md output).
- [ ] `condense_book`/`estimate_condense` accept `.pdf`; dry-run makes no LLM call.

## Acceptance criteria

- [ ] `PdfParser().parse(sample.pdf)` returns a populated, chapter-split `Document`.
- [ ] PDF input works through the CLI (`breviabook condense book.pdf ...`).
- [ ] All five validation gates green.

## Confidence score

6/10 — PDF heuristics are inherently fuzzy; de-risked by calibrating against a committed
fixture and keeping placement best-effort (images deduped, tables de-duplicated from text).
