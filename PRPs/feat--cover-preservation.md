# PRP: Preserve EPUB cover image + OPF cover metadata (F2)

> Product Requirement Prompt for **BreviaBook**. Fidelity QA sprint
> ([docs/fidelity-qa-2026-07.md](../docs/fidelity-qa-2026-07.md) F2).
> Source of truth: [docs/ROADMAP.md](../docs/ROADMAP.md). Operating rules: [CLAUDE.md](../CLAUDE.md).

## Goal

Round-trip the book cover through parse → IR → EPUB render so readers show a thumbnail and
page 1 is the cover image, not a blank sheet.

## Why

**Root cause (confirmed):** Source OPF has `<meta name="cover" content="cover"/>`. The parser
never reads it / never marks a cover asset on the Document. The EPUB renderer
(`epub_renderer.py` ~L118–139) emits neither `<meta name="cover">` nor
`properties="cover-image"`, and no dedicated cover document at spine head.

## Scope

**In scope:**
- IR: `DocumentMetadata.cover_image_id: str | None = None`
- EPUB parser: read OPF `<meta name="cover" content="{manifest-id}"/>`, resolve the manifest
  item via `resolve_archive_href` (zip-slip safe), register the asset, set `cover_image_id`
- ImageSelector Strategy A: always keep `cover_image_id` even if no `ImageBlock` references it
  (otherwise the cover is pruned as an orphan before render)
- EPUB renderer when `cover_image_id` is set and the asset exists:
  - manifest item with `properties="cover-image"`
  - legacy `<meta name="cover" content="{manifest-id}"/>`
  - `cover.xhtml` at spine head showing the image
  - if the first chapter is cover-only (single `ImageBlock` == cover), omit it from regular
    chapters to avoid a duplicate cover page
- Synthetic fixture EPUB with OPF cover meta + cover image (may or may not also appear in spine)

**Out of scope:**
- F1 / F3 / F4 / F5
- PDF “cover page” (PDF renderer has no OPF; optional later)
- Changing Strategy B vision ranking beyond inheriting selector’s kept set

## Non-negotiable constraints

- [ ] Clean-room; no `ebooklib` / GPL deps
- [ ] Zip-slip: all archive paths via `resolve_archive_href`
- [ ] Do not touch `htmlsan._SAFE_LINK_SCHEMES`
- [ ] Mock/fixture tests only

## Context & references

```yaml
- docs/fidelity-qa-2026-07.md          # F2
- breviabook/ir/models.py              # DocumentMetadata
- breviabook/parsers/epub_parser.py    # _parse_opf, parse(), _register_asset
- breviabook/images/selector.py        # Strategy A referenced set
- breviabook/render/epub_renderer.py   # _build_opf, render()
- breviabook/utils/security.py         # resolve_archive_href
- tests/test_epub_parser.py
- tests/test_epub_renderer.py
- tests/test_image_selector.py
```

## Implementation blueprint

1. **IR** — add optional `cover_image_id` on `DocumentMetadata`.
2. **Parser `_parse_opf`** — also return `cover_manifest_id: str | None` from
   `<meta name="cover" content="…"/>` (attribute `name`/`content`, local tag `meta`).
3. **Parser `parse`** — after walking spine chapters, if `cover_manifest_id` is in the
   manifest, resolve href → `_register_asset` → set
   `metadata = metadata.model_copy(update={"cover_image_id": image_id})`.
   Reuse existing id when the same path was already registered from a chapter `<img>`.
4. **ImageSelector** — `referenced.add(doc.metadata.cover_image_id)` when set.
5. **EpubRenderer.render**:
   - If cover asset present: write `OEBPS/cover.xhtml`, put cover page id first in spine,
     mark image manifest item with `properties="cover-image"`, emit
     `<meta name="cover" content="{mid}"/>` in metadata.
   - Dedupe: drop a leading chapter that is solely an `ImageBlock` for `cover_image_id`.
6. **Tests** (synthetic EPUB in tmp_path):
   - Parser sets `cover_image_id` and loads bytes even when cover image is NOT in any chapter
   - Selector keeps cover orphan
   - Rendered OPF has `properties="cover-image"`, `<meta name="cover"`, `cover.xhtml` in
     archive and first spine `itemref`
   - Round-trip: parse → render → parse keeps `cover_image_id` and image bytes

## Validation gates

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict breviabook
uv run pytest
uv run pip-licenses --partial-match --fail-on "General Public License;GPL" --ignore-packages pyphen
```

## Acceptance criteria

- [ ] Cover asset survives Strategy A when only referenced via `cover_image_id`
- [ ] Output EPUB has thumbnail-capable OPF cover metadata + visible cover.xhtml
- [ ] Existing round-trip tests for sample.epub (no cover) still pass
- [ ] All gates green

## Confidence score

8/10 — selector keep is mandatory or the fix is a no-op; dedupe of leading cover chapter is the
main behavioural nuance.
