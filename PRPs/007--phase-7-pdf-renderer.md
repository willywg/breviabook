# PRP: Phase 7 — PDF renderer

> Product Requirement Prompt for **Brevia**. Source of truth: [docs/ROADMAP.md](../docs/ROADMAP.md) §4, §5, §6, §10 (Phase 7), §14.
> Operating rules: [CLAUDE.md](../CLAUDE.md). Builds on PRP 006 (EPUB renderer). Completes the MVP (Phases 0–7).

## Goal

Render the IR to PDF: `brevia/render/pdf_renderer.py` builds a single self-contained HTML
document from the `Document` (images inlined as data URIs) and converts it with
`weasyprint`. Factor the block→HTML mapping into a shared module reused by the EPUB renderer.

## Why

- PDF is the third output format; with it Brevia produces EPUB + PDF + MD and the MVP is done.
- `weasyprint` (BSD) keeps us AGPL-free (no PyMuPDF) — ROADMAP §14.

## Scope

**In scope:**
- `brevia/render/html.py`: shared `esc()` + `block_to_html(block, image_src)` where
  `image_src(image_id) -> str | None` resolves the `<img>` source. EPUB and PDF both use it.
- Refactor `render/epub_renderer.py` to use `render/html.py` (output unchanged).
- `brevia/render/pdf_renderer.py`: `PdfRenderer` + module-level `build_html(doc)`. Images
  inlined as `data:` URIs (self-contained, deterministic). **Lazy-import** weasyprint inside
  `render()` so importing the module never fails when system libs are absent;
  `weasyprint_available()` helper.
- mypy override for `weasyprint.*`; CI step to install weasyprint's system libs so the PDF
  test runs there.
- Tests: HTML builder (always) + real PDF generation (skipped if weasyprint can't load).

**Out of scope:** PDF *parser* (Phase 8), CLI wiring (later), fancy theming.

## Non-negotiable constraints (CLAUDE.md / ROADMAP §14)

- [ ] No `PyMuPDF`/`fitz`. PDF output via `weasyprint` only.
- [ ] Code rendered verbatim in `<pre><code>` (escaped); images self-contained (data URIs).
- [ ] Importing `pdf_renderer` must NOT require weasyprint's system libs (lazy import).
- [ ] `build_html` output is deterministic.

## Context & references

```yaml
- docs/ROADMAP.md          # §4 weasyprint, §5 step 7, §10 Phase 7
- brevia/render/epub_renderer.py  # current block->xhtml to factor out
- brevia/render/base.py    # Renderer Protocol
- weasyprint: HTML(string=...).write_pdf(target)
- system libs (mac): brew install pango gdk-pixbuf libffi
- system libs (ubuntu CI): libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libffi-dev
```

## Design

- `block_to_html(block, image_src)` mirrors the EPUB mapping (h1–6, p, pre>code, blockquote,
  ul/ol, table, figure>img+figcaption); the image `src` comes from the injected resolver.
- EPUB passes a resolver returning the archive href; PDF passes one returning a data URI.
- `build_html(doc)`: book title H1, then a `<section class="chapter">` per chapter (with a
  small print CSS: page-break between chapters, monospace code, `img{max-width:100%}`).
  Skip the chapter-title heading when the body already opens with a heading (as MD renderer).
- `PdfRenderer.render`: lazy `from weasyprint import HTML`; `HTML(string=build_html(doc))
  .write_pdf(target)`.

## Implementation blueprint

1. `render/html.py` — `esc`, `block_to_html`.
2. Refactor `render/epub_renderer.py` to use them (keep behavior; tests stay green).
3. `render/pdf_renderer.py` — `build_html`, `weasyprint_available`, `PdfRenderer`.
4. `pyproject.toml` — add `weasyprint.*` to mypy overrides.
5. `.github/workflows/ci.yml` — apt-get install weasyprint system libs before tests.
6. Tests: `tests/test_pdf_renderer.py`.

### New / changed files

- `brevia/render/html.py` (new), `brevia/render/pdf_renderer.py` (new)
- `brevia/render/epub_renderer.py` (refactor), `pyproject.toml`, `.github/workflows/ci.yml`
- `tests/test_pdf_renderer.py` (new)

## Validation gates (must all pass)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict brevia
uv run pytest -q
uv run pip-licenses --fail-on "GPL"
```

### Feature-specific checks

- [ ] `build_html(doc)` contains chapter sections, verbatim escaped code, a data-URI `<img>`,
      a table, and the book title; no chapter-title duplication.
- [ ] Importing `brevia.render.pdf_renderer` succeeds even without weasyprint system libs.
- [ ] When weasyprint is available, `PdfRenderer().render` writes a file starting with `%PDF`.
- [ ] EPUB renderer tests still pass after the refactor.

## Acceptance criteria

- [ ] HTML is built deterministically and self-contained; PDF generated when libs present.
- [ ] Brevia can now emit EPUB + PDF + MD from one IR (MVP complete).
- [ ] All five validation gates green (PDF-generation test skipped if libs absent locally).

## Confidence score

8/10 — Logic is straightforward; the only environmental risk is weasyprint's system libs,
handled by lazy import + skip + a CI install step.
