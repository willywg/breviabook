# PRP: Class-driven bold/italic on blocks + image alignment (F4 + F7)

> Product Requirement Prompt for **BreviaBook**. Fidelity QA Round 2
> ([docs/fidelity-qa-2026-07.md](../docs/fidelity-qa-2026-07.md) Round 2 — F4 + F7).
> Source of truth: [docs/ROADMAP.md](../docs/ROADMAP.md). Operating rules: [CLAUDE.md](../CLAUDE.md).

## Goal

1. **F4** — Block-level CSS weight/style (e.g. `.legalnotice { font-weight: bold }` on a `<p>`)
   survives into `rich` as `<strong>` / `<em>`, coexisting with color spans.
2. **F7** — Centered figures (wrapper/figure `text-align: center`) produce
   `ImageBlock.align="center"` and EPUB/PDF HTML centers the `<figure>`.

## Why

- **F4 (confirmed against DMMT CSS).** "Notice of Rights / Liability / Trademarks" are
  `<p class="legalnotice">` with `.legalnotice { font-weight: bold }`. Color classes on *inline*
  spans already resolve; weight/style on the **block root** do not reach `rich`.
- **Precise locus (code check).** `_parse_style_attr` already maps `font-weight`/`font-style` →
  `bold`/`italic`; `parse_class_styles` stores them; `_effective_styles` merges class + inline;
  `_wrap` already emits `<strong>`/`<em>`. The gap: `sanitize_inline` only renders **children** of
  the root `Tag` and never applies the root's effective styles. Parser passes the `<p>`/`<h*>`
  Tag itself → class bold on the paragraph is dropped. Fix = apply root text styles after
  children (not a no-op change to `_effective_styles`, and **not** paragraph→heading promotion).
- **F7 (confirmed).** Source centers via wrapper `class="center"` (`.center { text-align: center }`).
  Text blocks inherit `align` (Phase A); `ImageBlock` has no `align`, so only captions center.

## Scope

**In scope:**
- F4: after child sanitize, wrap with root `_effective_styles` bold/italic/(strike/color) so
  block-class weight/style land in `rich`. Confirm `class_styles` already captures the props.
- F7: `ImageBlock.align: Align | None = None`; parser sets it via `block_align` on
  img / figure / container + one-level wrapper inherit (same Phase A pattern); `block_to_html`
  emits `text-align` on `<figure>`; Markdown ignores presentation (already).

**Out of scope:**
- F8 (hanging-indent bullets) — needs design decision.
- F1b (inline footnote targets) — deferred.
- Paragraph-bold → heading promotion (heuristic; forbidden for this batch).
- `_SAFE_LINK_SCHEMES` / `bbref:` handling (Phase B — do not touch).

## Non-negotiable constraints

- [ ] Clean-room; no GPL/AGPL deps.
- [ ] Do **not** modify `htmlsan._SAFE_LINK_SCHEMES` or the bbref href path.
- [ ] No heading promotion heuristics.
- [ ] Tests: synthetic fixtures + mock provider only; no real LLM.
- [ ] Invariant: when `rich` is set, `text == strip_tags(rich)`.

## Context & references

```yaml
- docs/fidelity-qa-2026-07.md          # Round 2 F4, F7
- breviabook/utils/htmlsan.py          # _parse_style_attr, _effective_styles, _wrap, sanitize_inline, block_align
- breviabook/ir/models.py              # ImageBlock (no align yet); Align on text blocks
- breviabook/parsers/epub_parser.py    # _walk, _emit_images, _add_image, _ALIGNABLE, inherit_align
- breviabook/render/html.py            # block_to_html ImageBranch; _align_attr
- breviabook/render/md_renderer.md     # drops align (document only)
- tests/test_htmlsan.py                # class italic/bold on spans; block_align
- tests/test_block_presentation.py     # Phase A center wrappers
```

## Implementation blueprint

### F4 — root block class weight/style → rich

1. Confirm `parse_class_styles(".legalnotice { font-weight: bold }")` → `{"legalnotice": {"bold": "1"}}`
   (already true via `_parse_style_attr`).
2. Extract the strike/italic/bold/color wrapping from `_wrap` into a small helper
   (e.g. `_apply_text_styles(inner, eff)`) and reuse it from `_wrap`.
3. In `sanitize_inline`, when the original `source` is a `Tag`, after joining children and
   whitespace-normalizing, apply `_apply_text_styles(inner, _effective_styles(root, cs))`.
   String inputs keep today's behaviour (styles come from child tags only).
4. Tests (Tag root, as the parser does):
   - `<p class="legalnotice">Notice</p>` + CSS → rich `<strong>Notice</strong>`, text `Notice`
   - class with `font-weight:bold` **and** `color` → both present (color span + strong)
   - `font-style:italic` on block class → `<em>…</em>`
   - Existing span-class tests still pass; `_SAFE_LINK_SCHEMES` unchanged

### F7 — ImageBlock.align

1. `ir/models.ImageBlock`: add `align: Align | None = None`.
2. Parser:
   - Pass `class_styles` + `inherit_align` into `_emit_images` / `_add_image`.
   - `align = block_align(img, cs) or block_align(container, cs) or inherit_align`.
   - Extend `_ALIGNABLE` with `"figure"` and `"img"` so one-level wrappers like
     `<div class="center"><figure>…</figure></div>` inherit (Phase A pattern).
3. `render/html.py`: on `ImageBlock`, put `_align_attr(block.align)` on the `<figure>`
   (same `style="text-align:…"` as text blocks).
4. Markdown: no change required (align not expressible; already discarded for text).
5. Tests (synthetic mini-EPUB):
   - wrapper `class="center"` + figure/img → `ImageBlock.align == "center"`
   - `block_to_html` figure has `style="text-align:center"`
   - MD render has no `text-align` / does not fail

### New / changed files

- `breviabook/utils/htmlsan.py`
- `breviabook/ir/models.py`
- `breviabook/parsers/epub_parser.py`
- `breviabook/render/html.py`
- `tests/test_block_class_styling.py` (or extend `test_htmlsan.py`) — F4
- `tests/test_image_align.py` (or extend `test_block_presentation.py`) — F7

## Validation gates (must all pass)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict breviabook
uv run pytest
uv run pip-licenses --partial-match --fail-on "General Public License;GPL" --ignore-packages pyphen
```

## Acceptance criteria

- [ ] `<p class="legalnotice">` with bold class → `rich` contains `<strong>`; plain `text` unchanged in meaning
- [ ] Color + weight on the same block class coexist in `rich`
- [ ] Centered figure wrapper → `ImageBlock.align="center"` and centered `<figure>` in HTML
- [ ] Markdown discards image align without error
- [ ] No heading promotion; `_SAFE_LINK_SCHEMES` / bbref untouched
- [ ] All five gates green; report real pytest collected count

## Confidence score

8/10 — F4 is a one-locus sanitize fix once root styles are applied; F7 mirrors Phase A align
inheritance. Risk: double-wrapping if a block already has inner `<strong>` (harmless visually);
wrapper inheritance for figure must not mis-apply align to multi-child wrappers (Phase A already
guards `single` child).
