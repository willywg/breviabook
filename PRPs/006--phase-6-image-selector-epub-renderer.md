# PRP: Phase 6 — Image selector (Strategy A) + EPUB renderer

> Product Requirement Prompt for **BreviaBook**. Source of truth: [docs/ROADMAP.md](../docs/ROADMAP.md) §5, §6, §7.1, §10 (Phase 6), §11, §14.
> Operating rules: [CLAUDE.md](../CLAUDE.md). Builds on PRP 001 (IR + EPUB parser) and PRP 002 (renderer base).

## Goal

Two things: (1) `breviabook/images/selector.py` — Strategy A image selection (structural: keep an
image iff its anchoring content survives; drop orphan assets and dangling refs). (2)
`breviabook/render/epub_renderer.py` — our own EPUB **builder** (zipfile, no ebooklib) that emits
a valid EPUB 3 re-embedding only the kept images. Validate via an IR round-trip: render EPUB
→ parse it back → equivalent document (§11).

## Why

- Strategy A is the default, cheap image policy (§7.1) and the structural guarantee that the
  output never has broken image links or unused embedded assets.
- The EPUB renderer is the headline output format; building it ourselves keeps us
  AGPL-free (§14).

## Scope

**In scope:**
- `breviabook/images/selector.py`: `ImageSelector.select(doc) -> SelectionResult` (cleaned
  `Document` + kept/dropped id lists). Strategy A only.
- `breviabook/render/epub_renderer.py`: `EpubRenderer` — builds mimetype + `container.xml` +
  `content.opf` (metadata/manifest/spine) + EPUB3 `nav.xhtml` + one XHTML per chapter +
  `images/`. Deterministic output (fixed identifier/timestamp) for testable round-trips.
- Tests: selector behavior + EPUB round-trip + zip/structure validity.

**Out of scope:** vision ranking (Strategy B, Phase 11), PDF renderer (Phase 7), CLI wiring.

## Non-negotiable constraints (CLAUDE.md / ROADMAP §14, §11)

- [ ] No `ebooklib` — build the EPUB with `zipfile` + string XHTML.
- [ ] `mimetype` is the FIRST zip entry and STORED (uncompressed) per the EPUB spec.
- [ ] Re-embed ONLY kept image assets; every `<img>` resolves to an embedded file.
- [ ] Code rendered verbatim inside `<pre><code>` (escaped, not reflowed) — survives round-trip.
- [ ] Output is deterministic (no wall-clock/random in the bytes) so round-trip tests are stable.

## Context & references

```yaml
- docs/ROADMAP.md          # §7.1 Strategy A, §11 round-trip, §6 layout
- breviabook/parsers/epub_parser.py  # the parser we round-trip against
- breviabook/render/base.py    # Renderer Protocol + image_filename()
- breviabook/ir/models.py      # Document/Chapter/Block/ImageAsset
# EPUB3: package.opf needs <meta property="dcterms:modified">; nav.xhtml needs epub:type="toc"
```

## Design

- **Selector (Strategy A):** referenced ids = ids of surviving `ImageBlock`s. Keep assets in
  that set; drop assets not referenced (section was cut). Also strip `ImageBlock`s whose asset
  is missing (dangling) so renderers never emit broken links. Return cleaned doc + report.
- **EPUB renderer:** map blocks → XHTML (`h1..h6`, `p`, `pre>code`, `blockquote`, `ul/ol>li`,
  `table>tr>th/td`, `figure>img+figcaption`). HTML-escape text. Manifest id per image derived
  from `image_id` (sanitized to an XML NCName, deduped vs chapter ids). Fixed `dcterms:modified`
  and a title-derived `dc:identifier` for determinism.

## Implementation blueprint

1. `images/selector.py` — `SelectionResult` (dataclass) + `ImageSelector.select`.
2. `render/epub_renderer.py` — `EpubRenderer`, `_block_to_xhtml`, `_build_opf`, `_build_nav`,
   filename/id dedup, zip assembly.
3. Tests: `tests/test_image_selector.py`, `tests/test_epub_renderer.py`.

### New / changed files

- `breviabook/images/selector.py`, `breviabook/render/epub_renderer.py`
- `tests/test_image_selector.py`, `tests/test_epub_renderer.py`

## Validation gates (must all pass)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict breviabook
uv run pytest -q
uv run pip-licenses --fail-on "GPL"
```

### Feature-specific checks

- [ ] Selector drops an unreferenced asset; strips a dangling `ImageBlock`; keeps referenced ones.
- [ ] Rendered file is a valid zip; `mimetype` is first and stored; contains container/opf/nav.
- [ ] Round-trip: `parse(sample.epub)` → render EPUB → `parse(rendered)` yields the same chapter
      titles, block-type sequence, verbatim code, and identical image bytes.
- [ ] EPUB embeds only kept images; every `<img>` href exists in the archive.

## Acceptance criteria

- [ ] `EpubRenderer().render(doc, tmp)` writes a valid, re-parseable EPUB.
- [ ] Strategy A selection is structural and deterministic.
- [ ] All five validation gates green.

## Confidence score

7/10 — EPUB 3 conformance details (nav, dcterms:modified, NCName ids) are fiddly; the
round-trip against our own parser is the safety net.
