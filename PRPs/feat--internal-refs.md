# PRP: Remap internal cross-references through the rebuilt book

> Product Requirement Prompt for **BreviaBook**. Follow-up to
> [feat--block-fidelity.md](feat--block-fidelity.md) Phase B. Source of truth:
> [docs/ROADMAP.md](../docs/ROADMAP.md) §3. Operating rules: [CLAUDE.md](../CLAUDE.md).
>
> **Status: DESIGN CAPTURED — IMPLEMENTATION PARKED.** Do not execute until a real fidelity
> QA pass on a condensed book shows internal links as a high-value gap. Condensation often
> drops footnote targets; many links would unwrap even with a perfect remap (limited ROI).

## Goal

Preserve **in-book** cross-references (TOC entries pointing at chapters/sections, footnote
superscripts) across parse → IR → render for a **rebuilt** EPUB/PDF. External `http(s)` /
`mailto` links stay as they are today (v0.2.0 `rich`).

## Why

- Today `htmlsan._safe_href` only allows `http://` / `https://` / `mailto:`, so `#frag` and
  relative XHTML hrefs are stripped at sanitize time (symptom: dead TOC / missing footnote
  marks in the condensed EPUB).
- **Trap:** widening the allowlist to pass source `#id` / `file.xhtml#id` through is **not** a
  fix. `EpubRenderer` rebuilds `chap-{n}.xhtml` from scratch; source archive paths are dead.
- Real fix: assign opaque IR `anchor_id`s at parse, store `bbref:{id}` in `rich`, rewrite to
  output locations at render; unwrap cleanly when the target block is gone.

## Why parked (product)

- Confidence ~6/10; touches href handling (security-adjacent).
- On a **condensed** book, many footnote/section targets may already be gone — remap then
  mostly unwraps. Validate need against a real QA book before spending the design budget.
- Phase A (`align` / `marker_*`) shipped at `95f1f6b` with higher visual ROI.

## Baseline (at authoring — HEAD after Phase A)

```text
$ git rev-parse --short HEAD
95f1f6b

$ uv run pytest --collect-only -q | tail -1
308 tests collected in 0.26s

$ uv run pytest -q | tail -1
307 passed, 1 skipped in 2.37s
```

Allowlist today (`breviabook/utils/htmlsan.py`):

```python
_SAFE_LINK_SCHEMES = ("http://", "https://", "mailto:")
```

## Scope

**In scope (when unparked):**

- Optional `anchor_id: str | None = None` on heading / paragraph / quote / list blocks.
- Parse-time rewrite of internal hrefs → `bbref:{anchor_id}` (never store source-relative paths).
- Extend `_safe_href` to accept **only** opaque `bbref:` + safe charset, in addition to the
  three external schemes. Still reject `javascript:`, `data:`, bare `#…`, relative paths.
- Render-time rewrite in `render/html.py` (+ EPUB chapter awareness) / MD degradation.
- Condenser shell copy of `anchor_id` (same pattern as `align` / `marker_*` / `ordered`).
- Translator: document that `bbref:` hrefs are structural; signature mismatch → existing
  rich downgrade.
- Mini-EPUB fixture: TOC chapter + footnote pair; tests for resolve + missing-target unwrap.

**Out of scope:**

- Patching the source EPUB in place.
- Full EPUB3 `epub:type="footnote"` / `aside` semantics beyond id+href remapping.
- Deep `nav.xhtml` hierarchy matching the source TOC (chapter-level nav already exists).
- Changing external link behavior.
- PDF-parser internal links (no reliable anchors from pdfplumber).

## Non-negotiable constraints

- [ ] Clean-room; no GPL/AGPL deps.
- [ ] Do **not** allowlist bare `#` or relative archive paths as a shortcut.
- [ ] Zip-slip: any path resolution uses `resolve_archive_href` only at parse time.
- [ ] `_SAFE_LINK_SCHEMES` gains at most the opaque `bbref:` form (or a dedicated branch in
      `_safe_href`) — never arbitrary relative hrefs.
- [ ] Mock provider only; report pytest counts from command output.
- [ ] `AGENTS.md` untracked.

## Design (from feat--block-fidelity §B — locked)

### IR

```python
# On HeadingBlock, ParagraphBlock, QuoteBlock, ListBlock:
anchor_id: str | None = None  # opaque, e.g. "a1" — not the source HTML id
```

Ephemeral at parse (need not persist on `Document` if all links are rewritten to `bbref` during
parse):

```text
(archive_path, fragment) → anchor_id
archive_path → chapter_index   # for chapter-only TOC hrefs
```

### `rich` wire form

| Stage | Form |
|---|---|
| IR after parse | `<a href="bbref:{anchor_id}">…</a>` |
| EPUB render | `chap-{n}.xhtml#{id}` or `#{id}` same chapter |
| PDF HTML | `#{id}` (single document) |
| MD | `[text](#id)` if target exists; else bare text |

### Parse (sketch)

1. Walk spine XHTML; on block `id` / `<a name>`, allocate `anchor_id`, record map entry.
2. Ids on wrappers: attach to nearest significant child block (prefer existing block; avoid
   empty synthetic paragraphs unless unavoidable).
3. Inline `<a href>`:
   - external → keep;
   - `#frag` / `rel#frag` → resolve + lookup → `bbref:` or unwrap;
   - file-only → first heading (or first block) of that chapter.
4. Never persist source-relative hrefs in `rich`.

### Render (sketch)

1. Map surviving `anchor_id` → `(chapter_index, html_id)`.
2. Emit `id=` on the block’s outer tag.
3. Rewrite `bbref:X` in `rich`; missing target → unwrap `<a>` (keep `<sup>` etc.).

### Condenser / translator

- Copy `anchor_id` from `source` in `parse_condensed_run` (headings are `keep` already).
- Translator `model_copy` preserves the field; prompt note for `bbref:` stability.

### Rejected alternatives

| Alt | Verdict |
|---|---|
| Allowlist `#` / relative hrefs | Rejected — rebuild invalidates paths; security smell |
| Keep source spine filenames in output | Rejected — couples renderer to archive layout |
| `Document.links` table outside `rich` | Deferred |
| New `FootnoteBlock` type | Deferred — remap covers QA without a new block kind |

## Implementation blueprint (when unparked)

1. Re-baseline pytest counts.
2. Commit this PRP if not already tracked at execute start.
3. `ir/models.py` — `anchor_id` on the four block types.
4. `parsers/epub_parser.py` — id collection + two-pass or deferred link rewrite within chapter /
   book (chapter-only targets need the full spine walk).
5. `utils/htmlsan.py` — accept `bbref:` in `_safe_href`; optional helper to rewrite/unwrap at
   render time may live in `render/html.py` instead.
6. `render/html.py` + `epub_renderer.py` — emit `id=`, rewrite refs (EPUB needs chapter index).
7. `render/md_renderer.py` — `#id` or unwrap.
8. `condense/common.py` — copy `anchor_id` from source.
9. Translate prompt one-liner for `bbref:`.
10. Fixtures + tests; five gates; one implementation commit.

### Suggested fixtures

- Mini-EPUB: chapter A with TOC links to chapter B and to `#sec-1`; chapter B with
  `<h2 id="sec-1">` and a footnote `<a href="#fn1"><sup>1</sup></a>` → `<p id="fn1">…</p>`.
- Assert parse→render EPUB contains working `href` to `chap-2.xhtml#…` and footnote target `id`.
- Assert dropping the footnote block (manual IR edit) unwraps the superscript link.

## Validation gates (when unparked)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict breviabook
uv run pytest -q
uv run pip-licenses --partial-match --fail-on "General Public License;GPL" --ignore-packages pyphen
```

### Feature-specific checks

- [ ] Source `#fn3` → IR `bbref:aN` → rebuilt EPUB href resolves to element with matching `id`.
- [ ] Cross-chapter TOC link → `chap-N.xhtml` or `chap-N.xhtml#…`.
- [ ] Missing target → link unwraps; no dangling href.
- [ ] External links unchanged; `javascript:` still stripped.
- [ ] Bare `#frag` / relative paths still rejected by sanitizer when not pre-rewritten by parser.
- [ ] Condensed path: surviving heading/footnote anchors still resolve; dropped targets unwrap.

## Acceptance criteria

- [ ] QA symptom (TOC + footnote superscripts) fixed on a real book **and** the mini fixture.
- [ ] No allowlist-only shortcut; remap is mandatory.
- [ ] Five gates green; counts from command output.
- [ ] Implementation commit only after explicit unpark / green light.

## Confidence score

6/10 — Architecture is clear; edge cases (wrapper ids, chapter-only hrefs, condense dropping
targets, translator mangling `bbref:`) need fixtures and a real-book QA pass before coding.
