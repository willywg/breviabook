# PRP: Phase 2 — Markdown renderer

> Product Requirement Prompt for **BreviaBook**. Source of truth: [docs/ROADMAP.md](../docs/ROADMAP.md) §5, §6, §10 (Phase 2), §11.
> Operating rules: [CLAUDE.md](../CLAUDE.md). Builds on PRP 001 (IR + EPUB parser).

## Goal

Render a `Document` (IR) to Markdown: `breviabook/render/md_renderer.py` writes a `.md` file
plus the referenced image assets to disk, with relative image links. This closes the first
real file→file round-trip (parse EPUB → render MD) and validates the IR end-to-end **with no
LLM**.

## Why

- Proves the IR carries everything needed to reconstruct a readable document.
- Markdown is the simplest renderer; it de-risks the renderer abstraction reused by the EPUB
  (Phase 6) and PDF (Phase 7) renderers.

## Scope

**In scope:**
- `breviabook/render/base.py`: `Renderer` Protocol (`render(doc, out_dir) -> Path`) + a shared
  helper for deriving image filenames from `ImageAsset`.
- `breviabook/render/md_renderer.py`: `MarkdownRenderer` — IR → Markdown string; writes
  `<stem>.md` and an `images/` folder with each asset.
- Tests: parse the fixture EPUB → render MD → assert every block type is represented and
  image files land on disk.

**Out of scope:** EPUB renderer (Phase 6), PDF renderer (Phase 7), full CLI pipeline wiring
(needs the condenser, Phase 4), image keep/drop selection (Phase 6).

## Non-negotiable constraints (CLAUDE.md)

- [ ] `code` blocks rendered verbatim inside a fenced block — never reflowed/escaped.
- [ ] Permissive-only deps (no new deps needed; stdlib + IR).
- [ ] Image filenames are stable and filesystem-safe; links are relative.

## Context & references

```yaml
- docs/ROADMAP.md          # §5 pipeline step [7], §6 render/ layout, §11 round-trip test
- breviabook/ir/models.py      # Document/Chapter/Block discriminated union
- breviabook/parsers/epub_parser.py  # produces the Document the renderer consumes
```

Markdown mapping:
- heading(level,text) -> `#`*level + text
- paragraph(text)     -> text
- code(lang,text)     -> fenced block ```lang … ```
- image(id,caption)   -> `![caption](images/<file>)`
- table(rows)         -> GitHub table (row 0 = header; escape `|`)
- quote(text)         -> `> text`
- list(items,ordered) -> `- item` / `1. item`

## Implementation blueprint

1. `render/base.py`:
   - `Renderer` Protocol with `name: str` and `render(self, doc, out_dir) -> Path`.
   - `image_filename(asset) -> str`: basename of `original_path` if present, else
     `image_id` + extension inferred from `mime` (png/jpg/gif/svg/webp; fallback `.bin`).
     Sanitize to a safe filename.
2. `render/md_renderer.py`:
   - `MarkdownRenderer` with `render(doc, out_dir, *, stem="condensed-book")`.
   - Write each `doc.images` asset under `out_dir/images/`; build `{image_id: rel_path}`.
   - Emit metadata title as an H1 if present, then each chapter (its title as a heading) and
     its blocks via a `_render_block` dispatch on `block.type`.
   - Join blocks with blank lines; write UTF-8 `<stem>.md`; return its `Path`.
3. Tests `tests/test_md_renderer.py` using `tmp_path`.

### New / changed files

- `breviabook/render/base.py`, `breviabook/render/md_renderer.py`
- `tests/test_md_renderer.py`

## Validation gates (must all pass)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict breviabook
uv run pytest -q
uv run pip-licenses --fail-on "GPL"
```

### Feature-specific checks

- [ ] parse `sample.epub` → render MD → `<stem>.md` exists and is non-empty.
- [ ] MD contains both chapter headings, the `python` fenced code block with verbatim text,
      a GitHub table, the list items, and the blockquote.
- [ ] Image link `![…](images/fig1.png)` present and `out_dir/images/fig1.png` exists with
      PNG bytes.
- [ ] No orphan image links (every link target was written).

## Acceptance criteria

- [ ] `MarkdownRenderer().render(doc, tmp)` returns the `.md` path and writes images.
- [ ] Round-trip parse→render reproduces all block types in Markdown.
- [ ] All five validation gates green.

## Confidence score

9/10 — Pure transformation over a known IR; main nuance is table/pipe escaping and image
filename derivation, both covered by tests.
