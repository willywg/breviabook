# PRP: Block-level fidelity — alignment, list markers, internal refs (phased)

> Product Requirement Prompt for **BreviaBook**. Design / fidelity follow-up after
> `feat--lists-quotes-condense.md` (lists/quotes structure) and `feat--inline-formatting.md`
> (v0.2.0 `rich` / `items_rich`). Source of truth: [docs/ROADMAP.md](../docs/ROADMAP.md) §3
> (IR), §6 (layout). Operating rules: [CLAUDE.md](../CLAUDE.md) / [AGENTS.md](../AGENTS.md).
>
> **This document is a design + phased plan.** Phase A is executable from this PRP after
> review. Phase B (internal refs) is designed here but **must ship as its own PRP** before
> implementation — weight and risk warrant a separate commit trail.

## Goal

Close three visual-fidelity gaps found in QA (condensed EPUB vs source), all caused by the IR
not modeling block-level presentation or link *targets*:

| # | Symptom | Root cause (verified) |
|---|---|---|
| 1 | Centered quotes / attributions / years render left-aligned | No block `align` (or equivalent) on IR; `block_to_html` emits bare `<p>`/`<blockquote>` |
| 2 | Colored / custom bullets become default discs | `ListBlock` only has `ordered`; no marker type/color; CSS `list-style*` never extracted |
| 3 | In-book TOC links and footnote superscripts disappear | `htmlsan._SAFE_LINK_SCHEMES` allows only `http://`/`https://`/`mailto:` — `#frag` and relative XHTML hrefs are dropped at sanitize time |

**End state (phased):**

- **Phase A (this PRP, implement after green light):** block alignment + list marker style round-trip parse → IR → EPUB/PDF; MD degrades cleanly; condense/translate preserve shell attrs.
- **Phase B (follow-up PRP):** remap internal anchors through parse→IR→render so rebuilt EPUB/PDF links resolve; drop cleanly when the target block is gone. External links untouched.

## Why

- Inline `rich` (v0.2.0) fixed emphasis/links/color *inside* text; these three bugs are
  **block chrome** and **graph edges between blocks** — orthogonal to `rich`/`items_rich`.
- The output EPUB is **rebuilt from scratch** (`EpubRenderer`), not a patch of the source.
  Source file layout (`Text/chap02.xhtml#note-3`) is meaningless after rebuild. Phase B must
  assign IR ids and rewrite hrefs at render — not merely widen the sanitizer allowlist.
- Lists/quotes now survive condensation as typed blocks; presentation attrs must ride that
  shell (same pattern as `ordered`).

## Baseline (verified at HEAD `5f7eb06`)

```text
$ git rev-parse --short HEAD
5f7eb06

$ uv run pytest --collect-only -q | tail -1
300 tests collected in 0.38s

$ uv run pytest -q | tail -1
299 passed, 1 skipped in 2.39s
```

### Code anchors (do not “fix” by guessing)

| Gap | Evidence |
|---|---|
| Align | `ir/models.py` — `ParagraphBlock` / `QuoteBlock` / `HeadingBlock` have `text`/`rich` only; no align |
| Align render | `render/html.py` L55–63 — `<p>…</p>`, `<blockquote>…</blockquote>` with no style/class |
| List marker | `ListBlock` = `items`, `ordered`, `items_rich` only (`ir/models.py` L69–74) |
| CSS extract | `htmlsan.parse_class_styles` / `_parse_style_attr` — only italic/bold/strike/**color** |
| Internal links | `htmlsan._SAFE_LINK_SCHEMES` L39; `_safe_href` L127–131 drops `#…` and relative paths |
| Rebuild | `epub_renderer.py` writes fresh `chap-{n}.xhtml` — no source hrefs reused |

### What is NOT broken (do not touch)

| Area | Note |
|---|---|
| External `http(s)`/`mailto` in `rich` | Already preserved; Phase B must leave them alone |
| Inline formatting (`rich` / `items_rich`) | v0.2.0 — extend, do not redesign |
| List/quote **types** through condense | `feat--lists-quotes-condense` — keep contract; only copy new shell fields from `source` |
| PDF input styling | pdfplumber has no CSS — PDF→IR stays plain (same as inline-formatting) |

## Scope

### Phase A — in scope (implement from this PRP)

- Optional IR fields for **block alignment** and **list marker presentation**.
- Extend CSS class/inline extraction for `text-align` and `list-style-type` (+ safe marker color).
- EPUB parser populates the fields; HTML path (EPUB+PDF) emits them; MD documents degradation.
- Condenser reassembly copies shell fields from `source` (like `ordered`); translator already
  `model_copy`s the block — verify new fields survive.
- Tests + five gates.

### Phase A — out of scope

- Internal / relative href remapping (Phase B).
- `list-style-image` / custom bullet GIFs (degrade to `square` or default; document).
- Per-item alignment or per-item marker overrides.
- Replicating fonts, margins, indent, drop caps.
- Changing condense JSON contract / prompts beyond copying shell attrs from source.
- PDF-parser presentation (no CSS available).

### Phase B — in scope (design here; **separate PRP before code**)

- IR `anchor_id` on targetable blocks + parse-time source `(file, fragment) → anchor_id` map.
- Sanitizer representation for internal refs (`bbref:…`) without allowing arbitrary relative paths.
- Render-time rewrite to `chap-N.xhtml#id` (EPUB) / `#id` (single-file PDF HTML).
- Survival through condense/translate; clean drop when target missing.
- Tests for TOC-style and footnote-style fixtures.

### Phase B — out of scope

- Patching / mutating the source EPUB in place.
- EPUB3 `epub:type="footnote"` semantics beyond id+href remapping.
- Deep nav.xhtml parity with source TOC hierarchy (chapter-level nav already exists).

## Non-negotiable constraints

- [ ] Clean-room only; no code from reference repos.
- [ ] No new runtime deps; no GPL/AGPL (`ebooklib` / `PyMuPDF` forbidden).
- [ ] `code` / `image` blocks never summarized or split.
- [ ] Do not break `text` / `rich` / `items_rich` invariants (`text == strip_tags(rich)` when set).
- [ ] Do not widen `_SAFE_LINK_SCHEMES` to arbitrary relative paths or `javascript:` — Phase B uses an
      opaque IR scheme + remap, not “pass source hrefs through”.
- [ ] Mock provider only in tests; no network.
- [ ] Report pytest collect/pass from **command output** before and after.
- [ ] `AGENTS.md` stays untracked.
- [ ] After green light: commit this PRP first, then one implementation commit for Phase A with
      `Co-Authored-By: Grok <noreply@x.ai>`. Phase B gets its own PRP commit + implementation commit.

---

## Design — Phase A: alignment + list markers

### A.1 IR shape (recommended)

Keep presentation on the **block shell**, parallel to `ordered` — never stuff block CSS into `rich`
(which is inline-only and would break the sanitizer allowlist / translator tag signature).

```python
Align = Literal["left", "center", "right"]  # omit "justify" for v1 (rare in tech ebooks)

class HeadingBlock(...):
    ...
    align: Align | None = None  # None → renderer omits style (UA default = left)

class ParagraphBlock(...):
    ...
    align: Align | None = None

class QuoteBlock(...):
    ...
    align: Align | None = None

class ListBlock(...):
    ...
    # Marker presentation (unordered mainly; ordered may set type=decimal/lower-alpha later)
    marker_type: Literal["disc", "circle", "square", "none"] | None = None
    marker_color: str | None = None  # same safe color grammar as htmlsan._COLOR_RE
```

**Retrocompat with `rich` / `items_rich`:** all new fields default `None`. Existing fixtures and
checkpoint JSON without the keys still validate (pydantic defaults). Condensation continues to
emit plain `text`/`items` (`rich=None`); presentation fields are copied from `source_*` in
`parse_condensed_run`, independent of the LLM.

**Why not a shared mixin base class?** Optional — a private `@dataclass` helper is fine, but
pydantic models today are flat and explicit; prefer explicit fields on the three/four blocks to
match the rest of `models.py`. Do not introduce a new `Block` base that changes the discriminator
union shape.

### A.2 Extraction (parser + css)

Extend `_parse_style_attr` / `parse_class_styles` (or a sibling `parse_block_styles`) to also
return:

| CSS property | IR field |
|---|---|
| `text-align: left\|center\|right` | `align` |
| `list-style-type: disc\|circle\|square\|none` (and common aliases) | `marker_type` |
| `color` on the **list element** (`ul`/`ol`) when present | `marker_color` |

Rules:

- Read from **inline `style`** on the block element and from **simple `.class` rules** (same
  regex engine as today — no full CSSOM).
- For paragraphs/quotes/headings: resolve effective `text-align` from the block tag’s classes +
  inline style (not from ancestors — keep v1 local; nested `div` wrappers with align on the
  wrapper are a known gap — optional stretch: if a wrapper `div` has a single block child and
  align, inherit once).
- For lists: resolve on the `ul`/`ol` element (not each `li`).
- Validate colors with existing `_safe_color` / `_COLOR_RE`.
- Ignore `list-style-image`, `::marker`, complex selectors — degrade to `marker_type=square` when
  `list-style-type` is unrecognized but a custom bullet is clearly intended, else leave `None`.

### A.3 Rendering

**`render/html.py` (EPUB + PDF):**

```html
<p style="text-align:center">…</p>
<blockquote style="text-align:center">…</blockquote>
<ul style="list-style-type:square;color:#c00">…</ul>
```

Prefer inline `style` on the element (matches how we emit `span style="color:…"` in `rich`) so
we do not need per-book CSS classes in the EPUB package. For lists: setting `color` on `ul`
colors markers in most readers; list-item text may inherit — if QA shows body text turning red,
wrap item content in `<span style="color:initial">` or set PDF/EPUB CSS
`li { color: inherit; }` / `ul { color: <marker> }` carefully. Prefer the smallest fix that
passes a fixture: e.g. `style="list-style-type:square"` +
`style="--bb-marker:#c00"` with a tiny shared CSS rule in `pdf_renderer._CSS` and EPUB chapter
CSS if we add one — **decision at implement time**, fixture-driven.

**`render/md_renderer.py`:** ignore `align` and marker fields (MD cannot express colored squares).
Document in module docstring: “block presentation degrades; inline `rich` still converts.”

### A.4 Condenser / translator shell copy

Today (`condense/common.py`):

```python
return ListBlock(items=items, ordered=source.ordered)  # drops future shell fields
return ParagraphBlock(text=text)                       # drops align
return QuoteBlock(text=raw.strip())                    # drops align
```

Phase A must copy `align` / `marker_*` from `source` whenever constructing replacement blocks.
Headings are `kind="keep"` — identity preserved; no change if we only add fields to the model.

Translator already: `block.model_copy(update={...})` — new fields persist automatically. Add one
regression test that a translated list keeps `marker_type`/`marker_color`.

### A.5 Alternatives (Phase A) — rejected

| Alt | Verdict |
|---|---|
| Encode align as `<div style="text-align">` inside `rich` | Rejected — `rich` is inline; block wrappers confuse sanitizer + translator signatures |
| Global CSS class names copied from Calibre (`class="calibre3"`) | Rejected — opaque, non-portable, couples IR to source stylesheet |
| Per-item marker color | Rejected — QA symptom is list-level; defer |
| `justify` alignment | Deferred — not in the reported QA cases |

### A.6 Implementation blueprint (Phase A)

1. Re-baseline pytest counts (command output).
2. Commit this PRP (`docs(prp): track block-fidelity phased plan`).
3. `ir/models.py` — add optional fields above.
4. `utils/htmlsan.py` — extend style parsing for `text-align` / `list-style-type`; export helpers
   e.g. `block_align(node, class_styles) -> Align | None`,
   `list_marker(node, class_styles) -> tuple[MarkerType | None, str | None]`.
5. `parsers/epub_parser.py` — set fields when emitting heading/paragraph/quote/list.
6. `render/html.py` — emit styles; `md_renderer.py` — no-op + docstring note.
7. `condense/common.py` — copy shell fields in `_parse_*_entry`.
8. Tests:
   - `tests/test_htmlsan.py` — class/inline → align / marker_type / marker_color; unsafe color dropped.
   - `tests/test_epub_parser.py` or new `tests/test_block_presentation.py` — fixture XHTML/CSS → IR.
   - `tests/test_html_renderer.py` (or existing) — IR → HTML contains `text-align` / `list-style-type`.
   - Condenser unit: source list with `marker_type="square"` survives mock condense.
   - Translator unit: shell fields survive `model_copy` path.
9. Five gates; report new collect/pass counts.
10. Single implementation commit.

### New / changed files (Phase A)

| Path | Change |
|---|---|
| `breviabook/ir/models.py` | `align`, `marker_type`, `marker_color` |
| `breviabook/utils/htmlsan.py` | block-style extraction helpers |
| `breviabook/parsers/epub_parser.py` | populate fields |
| `breviabook/render/html.py` | emit presentation |
| `breviabook/render/md_renderer.py` | document MD degradation |
| `breviabook/condense/common.py` | copy shell from `source` |
| `tests/test_*.py` | presentation + shell-copy coverage |
| `PRPs/feat--block-fidelity.md` | this file |

---

## Design — Phase B: internal cross-references (separate PRP)

### B.0 The trap (product constraint)

Widening `_safe_href` to accept `#id` or `foo.xhtml#id` **does not fix** the bug in a rebuilt
book: those paths point at the **source** spine layout. After `EpubRenderer` writes
`chap-1.xhtml`, `chap-2.xhtml`, …, a preserved `href="Text/chapter-notes.xhtml#fn3"` is a
dead link (or worse, a zip-slip / wrong-file smell if ever resolved against the archive).

**Correct fix:** parse assigns opaque IR anchor ids → rich stores `bbref:{anchor_id}` → render
resolves to the **output** location of the block that still carries that `anchor_id`. If the
target was dropped by condensation/image pruning, strip the link to plain styled text (keep
`<sup>` etc.).

### B.1 IR shape (recommended)

```python
class HeadingBlock(...):
    anchor_id: str | None = None  # opaque IR id, e.g. "a1", "a2" — NOT source HTML id verbatim

class ParagraphBlock(...):
    anchor_id: str | None = None

class QuoteBlock(...):
    anchor_id: str | None = None

class ListBlock(...):
    anchor_id: str | None = None

# Optional later: CodeBlock / TableBlock / ImageBlock if QA needs them as targets
```

Also during parse (ephemeral, not necessarily persisted on `Document` unless useful for debug):

```text
source_loc_index: dict[tuple[archive_path, fragment], anchor_id]
chapter_file_index: dict[archive_path, chapter_index]
```

Persisting the maps on `Document` is **optional**. Prefer regenerating nothing at render: only
`anchor_id` on blocks + `bbref` in `rich` are enough if every resolvable link was rewritten at
parse time.

### B.2 Representation inside `rich` (recommended)

Mirror the inline-image pattern (`data-image-id`):

| Stage | Form |
|---|---|
| After sanitize (IR) | `<a href="bbref:{anchor_id}">…</a>` |
| EPUB render | `<a href="chap-{n}.xhtml#{html_id}">` or `#{html_id}` same-file |
| PDF HTML render | `<a href="#{html_id}">` (single document) |
| MD render | `[text](#html_id)` if target exists; else bare text |

Extend `_safe_href` to accept **only**:

1. Existing external schemes (`http://`, `https://`, `mailto:`) — unchanged.
2. Exact opaque form `bbref:` + safe id charset (`[A-Za-z0-9._-]+`).

Reject: `javascript:`, `data:`, bare `#…`, relative paths, `../`, etc. The parser is the only
component that turns source `#frag` / `file#frag` into `bbref:…` **before** or **via** a
dedicated code path (not by trusting the sanitizer to pass source hrefs).

**Translator impact:** `inline_tag_signature` keys `a:{href}` — model must keep `bbref:…`
exactly (same as external URLs today). Document in translate prompt that `bbref:` hrefs are
structural and must not be altered. On signature mismatch → existing downgrade path
(`rich=None`, keep translated plain text).

### B.3 Parse algorithm (sketch)

For each spine XHTML `href` (archive path):

1. Walk blocks as today.
2. When a block element has `id` or `<a name>`: allocate `anchor_id = f"a{n}"`, set on the IR
   block; record `(archive_path, id) → anchor_id`. Prefer the **block** that owns the id
   (heading/p/li’s parent list/blockquote). If id sits on a wrapper `div`, attach to the first
   significant child block or synthesize a zero-text paragraph with that `anchor_id` only if
   needed — prefer attaching to nearest block.
3. When sanitizing inline `<a href>`:
   - External → keep (current).
   - `#frag` → lookup `(current_file, frag)`; if hit, emit `bbref:{id}`; else unwrap to text.
   - `relpath` / `relpath#frag` → `resolve_archive_href(current, relpath)` then lookup; if only
     file (no frag), map to the **first heading** (or first block) of that chapter’s IR — for
     TOC entries that point at whole chapters.
4. Never store source-relative hrefs in `rich`.

### B.4 Render algorithm (sketch)

1. Build `anchor_id → (chapter_index, html_id)` for all blocks that still have `anchor_id`.
   Choose `html_id` = sanitized `anchor_id` (already NCName-safe if we allocate carefully).
2. Emit `id="{html_id}"` on the block’s outer tag in `block_to_html`.
3. When emitting `rich`, rewrite `href="bbref:X"`:
   - If X in map and same chapter → `#html_id`
   - If X in map and other chapter → `chap-{n}.xhtml#html_id` (EPUB) / `#html_id` (PDF)
   - If X missing → unwrap `<a>` to inner HTML (keep `<sup>`).
4. EPUB `nav.xhtml` can stay chapter-level; in-body TOC paragraphs that are real content links
   become working links via the same rewrite.

### B.5 Condenser survival

- Headings (`keep`) retain `anchor_id` automatically.
- Paragraph/quote/list reassembly must copy `anchor_id` from `source` (same as Phase A shell
  copy). If condensation **merges** paragraphs someday, ids would collide — today we do not
  merge types; keep 1:1. If a structured run passthrough fails and chunk is verbatim, ids stay.
- If a footnote paragraph is deleted entirely (future aggressiveness), links to it unwrap at
  render — acceptable.

### B.6 Alternatives (Phase B) — rejected / deferred

| Alt | Verdict |
|---|---|
| Allowlist `#` and relative hrefs in sanitizer, hope render keeps files | **Rejected** — rebuild invalidates source paths; security smell |
| Keep source spine filenames in output EPUB | Rejected — couples renderer to parser archive layout; breaks PDF/MD |
| Separate `Document.links` table outside `rich` | Deferred — duplicates info already in text; harder for translator |
| Full footnote IR type (`FootnoteBlock`) | Deferred — remap covers QA (sup + target id) without new block kind |
| Phase B in the same implementation commit as Phase A | **Rejected for process** — different risk surface (security + graph); own PRP |

### B.7 Suggested follow-up PRP name

`PRPs/feat--internal-refs.md` — copy §B into an executable PRP with fixtures
(`tests/fixtures/` mini-EPUB: TOC chapter + footnote pair), acceptance criteria, and gates.
Do not start Phase B code from this umbrella file alone.

---

## Recommended execution order

```text
1. Review this PRP (design sign-off on A.1 / B.1–B.2 especially)
2. Commit this PRP (traceability)
3. Implement Phase A → five gates → one commit
4. Author feat--internal-refs.md from §B (may refine after Phase A lands)
5. Review → commit PRP → implement Phase B → gates → commit
```

**Do not** implement Phase B “while we’re in htmlsan anyway” without a dedicated PRP — easy to
ship a false fix (allowlist-only) that looks green in unit tests but fails on rebuilt books.

## Validation gates (Phase A — all must pass)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict breviabook
uv run pytest -q
uv run pip-licenses --partial-match --fail-on "General Public License;GPL" --ignore-packages pyphen
```

### Feature-specific checks (Phase A)

- [ ] Centered paragraph/quote in fixture → IR `align="center"` → HTML `text-align:center`.
- [ ] `ul` with `list-style-type:square` + red marker color → IR fields → HTML styles.
- [ ] MD output still valid; no requirement to express colored bullets.
- [ ] Condenser mock run copies `align` / `marker_*` from source blocks.
- [ ] Translator keeps marker/align via `model_copy`.
- [ ] Existing `rich` / external link tests still green.
- [ ] `_SAFE_LINK_SCHEMES` **unchanged** in Phase A (no premature allowlist widening).
- [ ] Before/after pytest counts from command output; `AGENTS.md` untracked.

### Feature-specific checks (Phase B — for the follow-up PRP)

- [ ] Source `#fn3` → IR `bbref:aN` → rebuilt EPUB href resolves to element with matching `id`.
- [ ] Cross-chapter TOC link → `chap-N.xhtml` or `chap-N.xhtml#…`.
- [ ] Missing target → link unwraps; no dangling href.
- [ ] External links unchanged; `javascript:` still stripped.
- [ ] Condensed book: heading anchors and surviving footnote targets still resolve.

## Acceptance criteria

### Phase A

- [ ] QA symptoms #1 and #2 addressed for EPUB/PDF on fixture + real-book spot check.
- [ ] MD degrades without errors.
- [ ] Five gates green; counts reported from command output.
- [ ] PRP committed before implementation; one implementation commit with Grok co-author trailer.

### Phase B (later)

- [ ] QA symptom #3 addressed via remap, not allowlist-only.
- [ ] Dedicated PRP + own implementation commit.

## Confidence score

**Phase A: 8/10** — Localized IR + CSS extract + render + shell copy; same pattern as
`ordered` / `rich`. Residual risk: messy Calibre CSS (`::marker`, bullet images) needing
explicit degrade rules.

**Phase B: 6/10** — Clear architecture, but edge cases (ids on wrappers, chapter-only hrefs,
condense dropping targets, translator mangling `bbref:`) need fixtures and careful unwrap
policy. That uncertainty is why it is a separate PRP.

## Reviewer decisions (green light — Phase A)

1. **Wrapper inheritance:** YES, one level — only when the wrapper `div` has a **single** block
   child. Do not build a general CSSOM resolver; revisit only if QA shows deeper nesting gaps.
2. **Marker color / bleed:** NEVER set `color` on `<ul>` (bleeds into `li` text). Emit marker
   color only via `li::marker { color: X }` (weasyprint + modern EPUB readers; old readers
   ignore → default marker color, acceptable). If `::marker` proves fiddly in fixtures, **defer
   `marker_color`** rather than hack `ul{color}` + reset. Prefer shipping `align` + `marker_type`
   cleanly.
3. **Phase B timing:** Author `feat--internal-refs.md` after A lands (capture design while the
   test book is fresh); **park implementation** until a real fidelity QA pass. Do not widen
   `_SAFE_LINK_SCHEMES` opportunistically.
