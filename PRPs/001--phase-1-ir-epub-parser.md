# PRP: Phase 1 — IR + EPUB parser

> Product Requirement Prompt for **BreviaBook**. Source of truth: [docs/ROADMAP.md](../docs/ROADMAP.md) §3, §6, §7.1, §10 (Phase 1), §11.
> Operating rules: [CLAUDE.md](../CLAUDE.md). Builds on PRP 000 (scaffold).

## Goal

Define the **Intermediate Representation** (`breviabook/ir/models.py`) and a license-clean
**EPUB parser** (`breviabook/parsers/epub_parser.py`) that turns an `.epub` into a `Document`,
extracting every image as an `ImageAsset` referenced by id. Prove it on a committed fixture
EPUB without losing blocks or images.

## Why

- The IR is the architectural keystone (ROADMAP §3): every later phase (chunk, condense,
  translate, render) operates on it.
- A working EPUB→IR parser unlocks Phase 2 (MD renderer) which validates the IR end-to-end
  with no LLM.

## Scope

**In scope:**
- `breviabook/ir/models.py`: `Document`, `DocumentMetadata`, `Chapter`, `ImageAsset`, and the
  `Block` discriminated union (heading/paragraph/code/image/table/quote/list) — all pydantic v2.
- `breviabook/parsers/base.py`: `Parser` Protocol (`parse(path) -> Document`).
- `breviabook/parsers/epub_parser.py`: our own EPUB reader using `zipfile` (stdlib) +
  `lxml`/`beautifulsoup4` — **no ebooklib**. Reads `container.xml` → `.opf` (spine + manifest),
  walks each spine XHTML into blocks, extracts `<img>` into `ImageAsset` + `ImageBlock`.
- A committed fixture `tests/fixtures/sample.epub` plus the small builder that produced it.
- Tests per ROADMAP §11 (parse loses no blocks/images; image ids unique; code intact).

**Out of scope:** PDF parser (Phase 8), TOC inference (Phase 8), renderers (Phase 2+),
any LLM call.

## Non-negotiable constraints (CLAUDE.md / ROADMAP §14, §12)

- [ ] No `ebooklib`/`PyMuPDF`. Use `zipfile` + `lxml`/`bs4` (already in the `formats` extra).
- [ ] **Zip-slip:** resolve every archive-internal href safely; reject paths escaping the
      archive root (use/extend `breviabook/utils/security.py`). We read in-memory, but still
      validate normalized paths.
- [ ] `code` blocks captured verbatim — never reflowed, escaped away, or split.
- [ ] Every original image becomes exactly one `ImageAsset` with a unique `image_id`.

## Context & references

```yaml
- docs/ROADMAP.md           # §3 IR shape, §6 layout, §7.1 image handling, §11 tests
- breviabook/utils/security.py  # safe_extract_path — extend for href resolution
- breviabook/config.py          # source_format value style
# EPUB structure:
- container.xml -> rootfile @full-path -> the .opf
- .opf: <manifest><item id href media-type> + <spine><itemref idref> (reading order)
- xhtml hrefs are relative to their own file; manifest hrefs relative to the .opf dir
- specs: https://www.w3.org/TR/epub-33/
```

## IR design (pydantic v2)

- `Block` = `Annotated[Union[Heading|Paragraph|Code|Image|Table|Quote|List], Field(discriminator="type")]`,
  each with a `Literal` `type` tag. Keeps (de)serialization clean for Phase 3 checkpoints.
- `ImageAsset(image_id, data: bytes, mime, original_path, alt_text)`.
- `Document(metadata, images: dict[str, ImageAsset], chapters: list[Chapter])`.
- `Chapter(title: str | None, blocks: list[Block])`.

## Implementation blueprint

1. `ir/models.py`: define the models above. Add a tiny helper `Document.iter_blocks()` for
   later phases. mypy-strict clean (no bare `Any`).
2. `parsers/base.py`: `Parser` Protocol + a `ParseError` exception.
3. `parsers/epub_parser.py`:
   - `EpubParser.parse(path) -> Document`.
   - Read `META-INF/container.xml` → OPF path. Parse OPF: build `manifest{id: (href, media_type)}`
     and ordered `spine` of idrefs. Pull title/author/language from `<metadata>` (Dublin Core).
   - Helper `_resolve(base_href, rel)` → normalized archive path; reject traversal.
   - For each spine XHTML: parse with bs4 (lxml backend), walk `<body>` children mapping
     `h1..h6→heading`, `p→paragraph`, `pre`/`pre>code→code`, `blockquote→quote`,
     `ul/ol→list`, `table→table`, `img→image`. One `Chapter` per spine doc; title = first
     heading or `<title>`.
   - For each `<img>`: resolve src via manifest → read bytes from the zip → create `ImageAsset`
     (id derived from manifest id or a stable counter) → emit `ImageBlock(image_id, caption)`.
     Caption from `alt`/adjacent `<figcaption>`.
4. Fixture: write `tests/fixtures/_build_sample_epub.py` that assembles a minimal valid EPUB
   (mimetype, container.xml, OPF with spine+manifest, 2 XHTML chapters covering every block
   type, 1 small PNG). Run it to produce `tests/fixtures/sample.epub`; commit both.
5. Tests `tests/test_epub_parser.py`.

### New / changed files

- `breviabook/ir/models.py`
- `breviabook/parsers/base.py`, `breviabook/parsers/epub_parser.py`
- `breviabook/utils/security.py` (extend with href resolver if needed)
- `tests/fixtures/_build_sample_epub.py`, `tests/fixtures/sample.epub`
- `tests/test_ir_models.py`, `tests/test_epub_parser.py`

## Validation gates (must all pass)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict breviabook
uv run pytest -q
uv run pip-licenses --fail-on "GPL"
```

### Feature-specific checks

- [ ] Parsing `sample.epub` yields the expected chapter count and metadata.
- [ ] Every image in the fixture appears once as an `ImageAsset` with a unique id, and is
      referenced by an `ImageBlock`; no orphan/duplicate ids.
- [ ] The fixture's code block round-trips byte-for-byte as a `CodeBlock` (no escaping/splitting).
- [ ] All seven block types are produced at least once across the fixture.
- [ ] A crafted entry with `../` traversal is rejected (zip-slip guard test).

## Acceptance criteria

- [ ] `EpubParser().parse(Path("tests/fixtures/sample.epub"))` returns a populated `Document`.
- [ ] No blocks or images lost vs. the fixture's known contents.
- [ ] All five validation gates green.

## Confidence score

7/10 — Risk is XHTML-walking edge cases (nested markup, figure/figcaption, href resolution).
Mitigated by controlling the fixture; real-world EPUB robustness hardens in later passes.
