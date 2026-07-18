# PRP: Remap internal cross-references through the rebuilt book (F1 / Phase B)

> Product Requirement Prompt for **BreviaBook**. Follow-up to
> [feat--block-fidelity.md](feat--block-fidelity.md) Phase B. Source of truth:
> [docs/ROADMAP.md](../docs/ROADMAP.md) §3. Operating rules: [CLAUDE.md](../CLAUDE.md).
>
> **Status: UNPARKED FOR REVIEW — DO NOT IMPLEMENT UNTIL APPROVED.**
> Gate: touches `htmlsan` href handling (security-adjacent). Design + this executable plan
> only; implementation waits for explicit green light after review of this PRP.

## Goal

Preserve **in-book** cross-references (TOC entries → chapters/sections, footnote superscripts)
across parse → IR → render for a **rebuilt** EPUB/PDF, so links stay functional **and** keep
the default `<a>` link color (the DMMT blue). External `http(s)` / `mailto` links stay as today.

Handle **both** pipelines:

| Mode | Targets | Required behaviour |
|---|---|---|
| **translate** (full-length) | Almost all survive | Remap → working `href` in output (ROI **HIGH** — DMMT QA) |
| **condense** | Many drop | Remap when target survives; **unwrap** to plain text (no dead `href`) when gone |

## Why — unpark justification (F1, confirmed)

Real-book QA: [docs/fidelity-qa-2026-07.md](../docs/fidelity-qa-2026-07.md) **F1**
(*Don't Make Me Think*, translate EN→ES). TOC titles render **bold black** instead of blue;
footnote markers lose their link.

Source pattern:

```html
<p class="toc-preface"><a href="part0004.html#pref01">
  <span class="pdred">PREFACE</span> <strong>About this edition</strong></a></p>
```

The blue is the UA default for `<a>`, not a color class. Today
`htmlsan._safe_href` (`breviabook/utils/htmlsan.py:165-168`) only keeps hrefs whose
`lower().startswith(_SAFE_LINK_SCHEMES)` where:

```python
_SAFE_LINK_SCHEMES = ("http://", "https://", "mailto:")  # htmlsan.py:44 — KEEP AS-IS
```

Internal `part0004.html#pref01` / `#fn1` fail the gate → `<a>` unwrapped at sanitize → lose
navigation **and** link color. `.pdred` still resolves (color classes), which is why PREFACIO
stays red while the title goes black.

## Why NOT the naive fix

**Do not** widen `_SAFE_LINK_SCHEMES` (or `_safe_href`) to accept `#id` / `file.xhtml#id`.
`EpubRenderer` rebuilds `chap-{n}.xhtml` from scratch (`epub_renderer.py` ~L76-79); source
archive paths are dead. Passing them through would emit dangling hrefs (and is a security smell).

**Correct fix (locked):** opaque IR `anchor_id` at parse → `bbref:{id}` in `rich` → rewrite to
**output** locations at render; unwrap if the target block is absent. Never emit a source path
or an unresolved `bbref:` / bare `#` in output.

## Baseline (at this refinement — HEAD `0c4d6f5`)

```text
$ git rev-parse --short HEAD
0c4d6f5

$ uv run pytest --collect-only -q | tail -1
326 tests collected in 0.39s

$ uv run pytest -q | tail -1
325 passed, 1 skipped in 2.85s
```

Post–fidelity batch context already on `main`: Phase A presentation (`align` / `marker_*`),
F3 `<br/>` in rich, F5 translated `dc:language`, F2 cover (`cover_image_id` + `cover.xhtml`).
This PRP must account for F2 chapter indexing (see § Design notes vs current code).

## Scope

**In scope (after approval):**

- Optional `anchor_id: str | None = None` on `HeadingBlock`, `ParagraphBlock`, `QuoteBlock`,
  `ListBlock`.
- Parse-time: index source ids → opaque ids; rewrite internal `<a href>` → `bbref:{id}` (or
  unwrap if unknown); never store source-relative paths in `rich`.
- `htmlsan`: dedicated `_safe_href` branch for opaque `bbref:` only.
  **`_SAFE_LINK_SCHEMES` stays exactly `("http://", "https://", "mailto:")`** — do not append
  `bbref:` to that tuple; do not allow `#` / relative paths.
- Render-time rewrite in `render/html.py` (shared EPUB/PDF) + chapter-aware resolve for EPUB;
  MD: `[text](#id)` or unwrap.
- Condenser shell-copy of `anchor_id` (same pattern as `align` / `marker_*`).
- Translator: prompt note that `bbref:` hrefs are structural; existing signature mismatch →
  rich downgrade still applies.
- Synthetic fixture: TOC + footnote; tests for translate-survive + condense-missing unwrap.

**Out of scope:**

- Patching the source EPUB in place.
- Full EPUB3 `epub:type="footnote"` / `aside` semantics beyond id+href remapping.
- Deep `nav.xhtml` hierarchy matching the source TOC (chapter-level nav already exists).
- Changing external link behaviour.
- PDF-parser internal links (pdfplumber has no reliable anchors).
- F4 (block class weight/style).

## Non-negotiable constraints

- [ ] Clean-room; no GPL/AGPL deps.
- [ ] **`_SAFE_LINK_SCHEMES` unchanged.** No allowlist of `#` or relative archive paths.
- [ ] Zip-slip: resolve source-relative link targets only via `resolve_archive_href` at parse.
- [ ] Never emit unresolved `bbref:`, source paths, or dangling `#frag` in rendered output.
- [ ] Mock provider only; report pytest counts from command output.
- [ ] `AGENTS.md` stays untracked; no push from the implementer without ask.

## Design (locked contract)

### IR

```python
# On HeadingBlock, ParagraphBlock, QuoteBlock, ListBlock:
anchor_id: str | None = None  # opaque, e.g. "a1" — NOT the source HTML id string
```

Ephemeral parse-only maps (not persisted on `Document`):

```text
(archive_path, fragment) → anchor_id     # fragment targets
archive_path → anchor_id                 # chapter-only href (no #frag) → first heading/block
```

### `rich` wire form

| Stage | Form |
|---|---|
| IR after parse | `<a href="bbref:{anchor_id}">…</a>` |
| EPUB render | `chap-{n}.xhtml#{anchor_id}` or `#{anchor_id}` if same chapter |
| PDF HTML | `#{anchor_id}` (single document) |
| MD | **always unwrap** `bbref:` `<a>` to inner text — MD cannot emit block `id=` on arbitrary paragraphs, so `[text](#id)` would be a dangling frag (forbidden) |
| Missing target (EPUB/PDF) | unwrap `<a>`, keep inner (`<sup>`, `<strong>`, …) — no href |

### `bbref:` validation (htmlsan)

Dedicated branch in `_safe_href` (not via `_SAFE_LINK_SCHEMES`):

```text
accepted iff href matches  ^bbref:[A-Za-z][A-Za-z0-9_-]*$
```

Anything else that is not http/https/mailto → `None` (unwrap). So:

- Parser-rewritten `bbref:a1` survives sanitize + translator re-sanitize.
- LLM-invented `#fn1` / `part0004.html#x` / `javascript:` still stripped.

### Parse algorithm (executable)

Mirror the existing `ImgResolver` pattern with an `HrefResolver`.

1. **Index pass (spine):** for each spine XHTML path `P` (via `resolve_archive_href`):
   - Parse soup; for every element with `id` or `<a name="…">`, allocate next `a{n}`, record
     `(P, frag) → anchor_id`.
   - Record `P →` anchor of the first heading in that file if any, else first id-bearing block,
     else allocate a synthetic id attached later to the first emitted text block (chapter-only
     TOC hrefs like `part0004.html`).
2. **Emit pass (existing `_walk`, extended):**
   - When creating a heading/paragraph/quote/list from an element that has `id`/`name` in the
     index, set `block.anchor_id`.
   - **Wrapper ids (one level):** if a structural wrapper (`div`/…) has `id` and exactly one
     alignable/list child block, attach that `anchor_id` to the child (same spirit as Phase A
     align inheritance). Prefer existing blocks; avoid empty synthetic paragraphs unless an
     id would otherwise be lost with no child.
   - `sanitize_inline(..., href_resolver=...)`: for each `<a href>`:
     - external (http/https/mailto) → keep as today;
     - `#frag` / `rel#frag` / file-only → `resolve_archive_href(current_chapter_path, href)` +
       index lookup → return `bbref:{id}` or `None` (unwrap);
     - never return a source-relative string.
3. **Invariant:** after parse, no `rich` / `items_rich` contains `href` other than
   `http(s)/mailto` or `bbref:…`.

### Render algorithm (executable)

1. Build `surviving: dict[anchor_id, (chapter_index, anchor_id)]` from the **same chapter list
   the EPUB writer will emit** (see F2 note below).
2. Emit `id="{anchor_id}"` on the block’s outer tag when `anchor_id` is set
   (`<h2 id="a3">`, `<p id="a7">`, …).
3. In `_inline` / list items (and MD equivalent): rewrite every `a[href^=bbref:]`:
   - lookup → set output href;
   - miss → unwrap `<a>` (keep children).
4. EPUB resolve: same chapter → `#{id}`; other → `chap-{n}.xhtml#{id}` (`n` 1-based as today).
5. PDF resolve: always `#{id}`.
6. **Never** leave `bbref:` in emitted HTML/MD.

### Condenser / translator

- **Headings** are already `keep` in `segment_blocks` — `anchor_id` rides for free.
- **Structured JSON array path** (`_parse_block_entry` / list / quote): copy
  `anchor_id=source.anchor_id` alongside `align` / `marker_*`.
- **Plain-string all-paragraph path** (`parse_condensed_run` when `raw` is `str`): today
  builds bare `ParagraphBlock(text=…)` and already drops `align`. For this PRP:
  - if `len(source_blocks) == 1` and exactly one output paragraph, copy shell
    (`align`, `anchor_id`) from that source;
  - if counts diverge (merge/split), **do not** invent `anchor_id` mappings — better unwrap
    at render than attach the wrong target. (Optional follow-up: also restore `align` the
    same way; not required beyond `anchor_id` unless trivial.)
- Translator: `model_copy` already preserves unknown-unmentioned fields when updating
  `text`/`rich`; add prompt one-liner that `bbref:` hrefs must be kept byte-stable.
  `inline_tag_signature` keys `a:{href}` — so mangling `bbref:` triggers existing downgrade.

### Design notes vs current code (must not ignore)

1. **F2 cover indexing:** `EpubRenderer` may prepend `cover.xhtml` and
   `_chapters_without_leading_cover` may drop a leading cover-only chapter before numbering
   `chap-{n}.xhtml`. Cross-ref rewrite **must** use that same post-dedupe chapter sequence
   when computing `n`, or TOC links point at the wrong file. Prefer a small shared helper
   (e.g. on the renderer) that both OPF emission and bbref resolve call — do not duplicate
   the dedupe predicate.
2. **`sanitize_inline` API:** today `(source, class_styles, img_resolver)`. Add optional
   `href_resolver: Callable[[str], str | None] | None` (parallel to images). Parse supplies
   it; translator leaves it `None` (bbref already in the string; `_safe_href` accepts it).
3. **Code/table/image blocks** do not get `anchor_id`. Links to ids that only existed on
   those structures unwrap (acceptable for v1; DMMT TOC/footnotes are heading/paragraph).
4. **Design still squares with code:** no conflict found that forces a different architecture.
   The string-condense shell gap and F2 index coupling are the only post-park deltas to bake in.

### Rejected alternatives

| Alt | Verdict |
|---|---|
| Allowlist `#` / relative hrefs in `_SAFE_LINK_SCHEMES` | **Rejected** — rebuild invalidates paths; security smell; forbidden by this gate |
| Keep source spine filenames in output | Rejected — couples renderer to archive layout |
| `Document.links` table outside `rich` | Deferred |
| New `FootnoteBlock` type | Deferred — remap covers F1 without a new block kind |

## Implementation blueprint (after green light only)

Ordered; one implementation commit after gates.

1. Re-baseline pytest counts at execute start; confirm `_SAFE_LINK_SCHEMES` still the 3-tuple.
2. `breviabook/ir/models.py` — `anchor_id` on the four block types.
3. `breviabook/utils/htmlsan.py` — `href_resolver` plumbing; dedicated `bbref:` branch in
   `_safe_href`; **do not** mutate `_SAFE_LINK_SCHEMES`; helpers optional
   (`is_bbref`, `bbref_id`).
4. `breviabook/parsers/epub_parser.py` — id index pass + emit pass with resolver; set
   `anchor_id` on blocks; wrapper-id inheritance (one level).
5. `breviabook/render/html.py` — `id=` on blocks; `rewrite_bbrefs` inside `_inline` (needs a
   resolve callback). Thread resolve from EPUB/PDF callers.
6. `breviabook/render/epub_renderer.py` — build surviving map from post-dedupe chapters;
   pass chapter-aware resolve into `block_to_html` / `_inline` (signature may grow an
   optional `ref_resolve` alongside `image_src`).
7. `breviabook/render/pdf_renderer.py` / `build_html` — same with `#{id}` resolve.
8. `breviabook/render/md_renderer.py` — **unwrap all `bbref:` links to inner text** (never
    `[text](#id)`; MD has no place to put matching `id=` on arbitrary blocks).
9. `breviabook/condense/common.py` — copy `anchor_id`; fix 1:1 string-paragraph shell copy.
10. `breviabook/translate/translator.py` — prompt mention of `<br/>`-style structural
    `bbref:` stability (one line).
11. Tests + five gates; single feat commit with `Co-Authored-By: Grok <noreply@x.ai>`.

### Files expected to change

| File | Role |
|---|---|
| `breviabook/ir/models.py` | `anchor_id` fields |
| `breviabook/utils/htmlsan.py` | `href_resolver` + `bbref:` accept/reject |
| `breviabook/parsers/epub_parser.py` | index + rewrite + attach |
| `breviabook/render/html.py` | emit `id=`, rewrite/unwrap `bbref:` |
| `breviabook/render/epub_renderer.py` | chapter-aware resolve (F2-safe) |
| `breviabook/render/pdf_renderer.py` | `#{id}` resolve |
| `breviabook/render/md_renderer.py` | MD resolve/unwrap |
| `breviabook/condense/common.py` | shell copy `anchor_id` |
| `breviabook/translate/translator.py` | prompt note |
| `tests/test_internal_refs.py` (new) | fixture + survive + unwrap + security |
| `tests/test_htmlsan.py` | `bbref:` accept; `#` / relative still rejected; schemes tuple unchanged |

### Suggested fixture + tests

Synthetic mini-EPUB (tmp_path zip, same style as `test_cover_preservation` /
`test_block_presentation`):

- **ch-toc.xhtml:** paragraph with
  `<a href="ch-body.xhtml#sec-1"><strong>About this edition</strong></a>` and
  `<a href="ch-body.xhtml">Chapter body</a>` (file-only).
- **ch-body.xhtml:** `<h2 id="sec-1">Section</h2>`,
  `<p>See note <a href="#fn1"><sup>1</sup></a>.</p>`,
  `<p id="fn1">Footnote body.</p>`.

Assertions:

1. **Parse:** TOC/footnote `rich` contains `bbref:a…` only (no `ch-body.xhtml`); target
   blocks have `anchor_id`; `text == strip_tags(rich)`.
2. **Translate-survive (render):** parse → render EPUB → OPF/XHTML contain
   `href="chap-2.xhtml#a…"` (or whatever index the body chapter gets) and matching `id="a…"`;
   footnote `<a>` preserved around `<sup>`.
3. **Condense-missing:** take parsed IR, delete the footnote `ParagraphBlock`, render →
   marker is `<sup>1</sup>` **without** wrapping `<a>`; no `bbref:` / `#fn1` in output.
4. **Security:** `sanitize_inline('<a href="#x">t</a>') == "t"`;
   `sanitize_inline('<a href="javascript:evil()">t</a>') == "t"`;
   `sanitize_inline('<a href="bbref:a1">t</a>') == '<a href="bbref:a1">t</a>'`;
   `htmlsan._SAFE_LINK_SCHEMES == ("http://", "https://", "mailto:")`.
5. **External unchanged:** `https://` links still round-trip.

## Validation gates (when implementing)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict breviabook
uv run pytest
uv run pip-licenses --partial-match --fail-on "General Public License;GPL" --ignore-packages pyphen
```

Report collected/passed counts from command output (not memory).

## Acceptance criteria

- [ ] DMMT F1 symptom addressable: TOC/footnote `<a>` survive translate path with working href
      + link styling; confirmed on mini fixture (real-book re-QA by reviewer).
- [ ] Condense path: missing targets unwrap cleanly (no dead links, no leaked `bbref:`).
- [ ] Markdown: `bbref:` links unwrap to plain inner text — never `[text](#id)` / dangling frags.
- [ ] `_SAFE_LINK_SCHEMES` byte-identical to today’s 3-tuple; no `#`/relative allowlist.
- [ ] F2 cover dedupe does not mis-number cross-chapter hrefs.
- [ ] Five gates green after implementation commit.
- [ ] Block `id="{anchor_id}"` values stay disjoint from OPF/manifest ids (`chap-*`, `img-*`,
      `nav`, `cover-page`) — opaque `a{n}` prefix is sufficient; document if no shared uniquifier.

## Open questions for reviewer (non-blocking if defaults accepted)

1. **Output HTML `id`:** use opaque `anchor_id` as-is (`id="a1"`) — **default yes**.
2. **String-condense shell copy:** only when 1 source paragraph → 1 output paragraph —
   **default yes** (documented above).
3. **Synthetic chapter anchor** when a spine file has zero ids/headings but is TOC-linked
   file-only: attach to first emitted paragraph/heading — **default yes**; if the chapter
   emits no blocks, unwrap those links.

## Confidence score

7/10 — Architecture unchanged and still matches the code; F1 real-book proof raises ROI for
translate. Remaining risk is parse edge cases (wrapper ids, empty chapters) and keeping EPUB
chapter indices aligned with F2 cover dedupe — both called out with defaults above.

**STOP after committing this PRP. Await explicit approval before any code change.**
