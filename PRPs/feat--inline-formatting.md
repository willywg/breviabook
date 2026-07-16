# PRP: inline formatting — preserve emphasis, links, color (and later inline images)

> Product Requirement Prompt for **BreviaBook**. Source of truth: [docs/ROADMAP.md](../docs/ROADMAP.md).
> Operating rules: [CLAUDE.md](../CLAUDE.md).

## Goal

Preserve inline text formatting — **bold, italic, links, inline code, and color** — through the
whole pipeline (parse → IR → translate → render), so the output keeps the styling of the source
instead of flattening every heading, link and emphasized run to plain text.

Staged:
- **Stage 1 (this PRP's core):** emphasis (`em/strong`), links (`a`), inline code (`code`),
  super/subscript, strikethrough, and **color** (inline `style` *and* CSS-class-resolved, e.g.
  Calibre's `pdred1`). ~444 of the 585 style events measured in the first ~30 pages of the test book.
- **Stage 2 (follow-up, same branch):** inline `<img>` embedded mid-text (e.g. the hand-drawn
  strikethrough "needless" inside a chapter title). ~141 events. Harder — couples to the image
  asset system and the Strategy-A selector. Do **after** Stage 1 is green and validated.

## Why — root cause (measured, not guessed)

Today every text block stores plain `str`. The EPUB parser calls `get_text(" ", strip=True)`,
which **collapses all inline markup at parse time** — before condensation or translation runs.
So the styling is gone before any prompt sees it; no "translate but keep the HTML" instruction
can recover information that was already discarded.

Measured on the test book (Don't Make Me Think), first ~12 XHTML files (~30–40 pages):

| Inline style in source | count | IR keeps today |
|---|---:|---:|
| `<a>` links | 132 | 0 |
| `<i>/<em>` italic | 113 | 0 |
| `<b>/<strong>` bold | 73 | 0 |
| `<span class>` color | 126 | 0 |
| `<img>` inline | 141 | 0 (Stage 2) |
| **total** | **585** | **0** |

This is NOT a translation bug: a plain parse→render round-trip loses the same styling. The fix
is architectural — give the IR a place to hold inline formatting, stop the parser flattening it,
and have the renderers/translator carry it through.

## Design (the two load-bearing decisions)

### 1. A parallel `rich` field — never change what `text` means

- `HeadingBlock`, `ParagraphBlock`, `QuoteBlock` gain `rich: str | None = None`.
- `ListBlock` gains `items_rich: list[str] | None = None`.
- `rich` holds **sanitized inline HTML**; it is `None` when the block has no inline markup
  (pure text stays simple — existing round-trip/plain-text tests are unaffected).
- `text` stays the plain flattened string, exactly as today. **Every existing `.text` consumer is
  untouched:** `utils/tokens.py` (chunk budgeting), the `Chunker`, `Condenser`, `Synthesizer`.
  This is what keeps the condense path regression-free.

Invariant: when `rich` is set, `text == strip_tags(rich)`. The parser establishes it; the
translator re-establishes it after translating `rich`.

### 2. Normalize to a small semantic allowlist, sanitized

The parser converts source markup — real tags **and** CSS classes — into a normalized, sanitized
subset. New module `breviabook/utils/htmlsan.py` (mirrors `utils/security.py`'s defensive intent:
untrusted EPUB HTML must never reach our output un-sanitized).

Allowed output tags / attributes (everything else is unwrapped to its text, text is escaped):

| Output | Produced from |
|---|---|
| `<em>` | `<em>`, `<i>`, class/style `font-style:italic` |
| `<strong>` | `<strong>`, `<b>`, class/style `font-weight:bold\|700` |
| `<a href="…">` | `<a href>` — only `http(s):`/`mailto:` schemes; drop `javascript:` etc. |
| `<code>` | `<code>` (inline; block `<pre><code>` stays a `CodeBlock`) |
| `<sup>` / `<sub>` | `<sup>` / `<sub>` |
| `<s>` | `<s>`, `<strike>`, `<del>`, `text-decoration:line-through` |
| `<span style="color:…">` | inline `style="color:…"` **or** a CSS class whose rule sets `color` |

- **Color resolution:** the parser reads the EPUB stylesheet(s) once per book, extracting simple
  `.class { color: … ; font-style: … ; font-weight: … ; text-decoration: … }` declarations into a
  `{class: styles}` map, so `<span class="pdred1">` becomes `<span style="color:#…">`. Only these
  four properties are read; the value is validated (a `#hex`, `rgb(...)`, or a CSS color keyword)
  and anything else dropped. No external stylesheet fetches.
- The sanitizer is the single choke point: it takes a parsed fragment and returns a safe inline-HTML
  string. It is used by the parser (on the way in) **and** by the translator (to re-sanitize the
  model's output — never trust an LLM to hand back clean tags).

## Scope

**In scope (Stage 1):** the IR fields, `utils/htmlsan.py`, EPUB-parser inline extraction + CSS
class-color resolution, all three renderers emitting `rich`, translator preserving+validating tags,
and tests. **Out of scope (Stage 1):** inline images (Stage 2); PDF-parser rich text (pdfplumber
gives no styling — PDF input stays plain, acceptable); replicating exact fonts/sizes/margins.

## Non-negotiable constraints (CLAUDE.md / ROADMAP §14)

- [ ] Clean-room; no copied code. No new GPL/AGPL deps (`beautifulsoup4`/`lxml` already present).
- [ ] `code` and `image` blocks never summarized/split — inline `<code>` is *inline emphasis*, not a
      `CodeBlock`; block code is unchanged.
- [ ] Untrusted EPUB HTML is **sanitized to the allowlist** before it enters `rich` and again after
      translation. No `javascript:`/`data:` hrefs, no event handlers, no arbitrary `style`.
- [ ] `text` semantics unchanged → condense path has zero behavioural change (regression test).

## Implementation blueprint (Stage 1)

1. **`breviabook/utils/htmlsan.py`** (new)
   - `sanitize_inline(node_or_html, *, class_styles=None) -> str`: walk a BS4 fragment, emit
     normalized sanitized inline HTML per the table above; escape all text; unwrap disallowed tags.
   - `strip_tags(rich) -> str`: plain text of a rich string (the `text` fallback).
   - `inline_tag_signature(rich) -> Counter`: multiset of (tag,attrs) — used by the translator to
     verify the model didn't drop/add/rewrite tags.
   - `parse_class_styles(css_text) -> dict[str, dict]`: extract `color/font-style/font-weight/
     text-decoration` per class from stylesheet text (regex, no full CSS engine).
   - `has_inline_markup(...) -> bool`: whether a fragment carries any styling worth storing (else
     `rich=None`).

2. **`breviabook/ir/models.py`** — add `rich` / `items_rich` optional fields (default `None`).

3. **`breviabook/parsers/epub_parser.py`**
   - Load stylesheet(s) from the manifest once; build the `class_styles` map; thread it into `_walk`.
   - For heading/paragraph/quote: compute `text` (as today) **and** `rich = sanitize_inline(el,
     class_styles)`; store `rich` only when `has_inline_markup` (else `None`).
   - For list items: same, producing `items_rich` (or `None` when no item has markup).
   - Do not alter code/table/image handling.

4. **Renderers** — emit `rich` when present, else the escaped `text`:
   - `render/html.py`: `heading/paragraph/quote/list` use `block.rich`/`items_rich` verbatim
     (already sanitized) instead of `esc(text)`; plain blocks unchanged. Shared by EPUB+PDF.
   - `render/md_renderer.py`: convert `rich` → Markdown (`<em>`→`*`, `<strong>`→`**`, `<a>`→`[](
     )`, `<code>`→`` ` ``) and pass through `<span style>`/`<s>`/`<sup>` as inline HTML (valid GFM).
   - PDF: inherits `render/html.py`; ensure the CSS keeps link/color styling visible.

5. **`breviabook/translate/translator.py`**
   - Translatable unit uses `rich` when present. Prompt gains: "a segment may contain inline HTML
     (`<em><strong><a><code><span><sup><sub><s>`); keep every tag and attribute exactly, translate
     only the text between tags; never add/remove/reorder tags."
   - After parsing the reply: `sanitize_inline` the returned segment, then compare
     `inline_tag_signature` to the source. On mismatch, **fall back to the source `rich`** (same
     resilience rule as batch failures — never emit corrupted markup). Set the block's `text` to
     `strip_tags` of the accepted rich.
   - `count_translatable_units` unchanged (counts the same units).

6. **Tests** — `tests/test_htmlsan.py` (sanitize/normalize/strip/signature/class-color/malicious
   input dropped), parser tests (i/b/a/span-class → rich; plain stays `rich=None`), renderer tests
   (rich round-trips to EPUB/MD/PDF-HTML; MD conversion), translator tests (tags preserved; tag
   tampering falls back), and a **regression** test asserting the condense path output is byte-identical
   to before (plain `text` still drives it).

## Validation gates (must all pass)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict breviabook
uv run pytest -q
uv run pip-licenses --fail-on "GPL"
```

### Feature-specific checks

- [ ] `<span class="pdred1">Guiding Principles</span>` (class color from the stylesheet) renders red
      in EPUB/PDF and survives translation.
- [ ] `<i>`, `<b>`, `<a href>` become `<em>`, `<strong>`, `<a>` in `rich`; a plain paragraph keeps
      `rich=None`.
- [ ] A malicious span (`<span onclick=…>`, `<a href="javascript:…">`, `<script>`) is stripped to
      safe output.
- [ ] Translator keeps tags: a segment `<strong>Don't</strong> make me think` returns with the
      `<strong>` intact; if the model mangles the tags, the source markup is kept.
- [ ] Condense path: existing pipeline tests unchanged; a regression test proves identical output.
- [ ] All tests use the mock provider — no real LLM call.

## Acceptance criteria

- [ ] The translated Contents page of the test book shows red section headings, blue/emphasized
      chapter links, and italic subtitles — matching the source's styling (minus inline images).
- [ ] No plain-text-only regression: books without inline markup render exactly as before.
- [ ] All gates green.

## Confidence score

7/10 — Mechanically clear, but three sharp edges: (a) sanitizer completeness/security, (b)
class-color resolution from real (messy) Calibre CSS, (c) the translator keeping tag integrity
across a language change. Each has an explicit fallback (drop to safe/plain) so failures degrade
rather than corrupt.
