# Fidelity QA Sprint — DMMT EN→ES (2026-07-18)

Real-book fidelity pass on *Don't Make Me Think, Revisited* (`translate`, gemini-3-flash-preview,
v0.3.0). Each finding was confirmed against the **generated EPUB internals and the source markup**,
not just the screenshot — root cause is cited to `file:line`. Severity = reader-visible impact ×
frequency. Effort = rough implementation size.

Source: ` Krug… libgen.li.epub` · Output: `Don't Make Me Think (Español v0.3.0 QA - BreviaBook).epub`

---

## F1 — Internal links & their color are lost (TOC titles, footnotes)  ·  SEV: HIGH · EFFORT: HIGH
**Symptom** (Image #8): in the source TOC the chapter titles ("About this edition", "Read me
first"…) are **blue**; in the translation they render **bold black**. Footnote markers likewise.

**Root cause (confirmed).** The blue is *not* a color class — the title is blue because it sits
inside an internal anchor:
```html
<p class="toc-preface"><a href="part0004.html#pref01">
  <span class="pdred">PREFACE</span> <strong>About this edition</strong></a></p>
```
`htmlsan._SAFE_LINK_SCHEMES` (`utils/htmlsan.py:44`) only allows `http/https/mailto`, and the href
gate at `htmlsan.py:165` drops everything else. So the internal `<a>` is stripped → the text loses
the `<a>` default link color (blue) and the navigation target. The red `.pdred` label survives
(color classes are resolved), which is why PREFACIO stays red but the title goes black.

**Fix.** This is **Phase B** — already designed in `PRPs/feat--internal-refs.md`. Remap intra-book
anchors/ids across parse→render under a `bbref:{id}` scheme so internal links survive *functional
and styled*. Do NOT just widen the allowlist (that keeps a dangling href with no target). The
QA above is the real-world justification to **unpark it**.

## F2 — Book cover is dropped (blank page 1 + no reader thumbnail)  ·  SEV: HIGH · EFFORT: MED
**Symptom** (Image #3): source page 1 is the red cover; translation page 1 is blank. The file also
shows no cover thumbnail in a reader.

**Root cause (confirmed).** Source OPF declares `<meta name="cover" content="cover"/>`; the
generated OPF metadata block (`render/epub_renderer.py`, ~L118-139) emits **no** `<meta
name="cover">` and no `properties="cover-image"` item, and the parser never marks a cover image.

**Fix.** (a) Parser: read the OPF `<meta name="cover">` → resolve the cover asset → mark it on the
Document (e.g. `metadata.cover_image_id`). (b) EPUB renderer: emit the cover in the manifest
(`properties="cover-image"`) + legacy `<meta name="cover">` + a cover.xhtml at spine head.

## F3 — `<br>` line breaks flattened to spaces (credits merged into one blob)  ·  SEV: MED · EFFORT: LOW
**Symptom** (Image #7): source lists each credit on its own line (Editor / Project Editor /
Production Editor…); translation runs them together as one flowing paragraph.

**Root cause (confirmed).** `htmlsan._render_child` (`utils/htmlsan.py:221-222`) maps `<br>` → `" "`.
Intra-paragraph line breaks become spaces and the visual line structure is gone.

**Fix.** Preserve `<br/>` in the sanitized `rich` output (it is already safe/void). Renderers emit
`<br/>` verbatim (EPUB/PDF) and `  \n` / `\n` for Markdown. `strip_tags` maps `<br>`→space so the
plain `text` projection driving condensation is unchanged. Small, localized.

## F4 — Block-level class styling lost (bold sub-headings render plain)  ·  SEV: MED · EFFORT: MED
**Symptom** (Images #5, #7): "Notice of Rights / Notice of Liability / Trademarks" are bold
sub-headings in the source; translation renders them as plain body paragraphs. Title-page subtitle
loses its weight/size too.

**Root cause (confirmed).** These are `<p class="legalnotice">` / `<p class="copy">` — paragraphs
whose weight comes from a CSS class. We resolve **color** classes (`.pdred`→inline color) but not
**font-weight / font-style / font-size** classes, and heading detection is tag-based (`<p>` is never
promoted), so class-styled emphasis on a paragraph is dropped.

**Fix.** Extend the class→style resolution in `htmlsan` to also carry `font-weight:bold` /
`font-style:italic` (wrap as `<strong>`/`<em>` in `rich`). Optionally promote an all-bold short
`<p>` that acts as a heading — but keep this conservative to avoid false promotions.

## F5 — Translated EPUB keeps `dc:language = en`  ·  SEV: LOW · EFFORT: LOW
**Symptom** (not visible in screenshots; found during QA): the Spanish output's OPF says
`<dc:language>en</dc:language>`.

**Root cause (confirmed).** `render/epub_renderer.py:118` uses `meta.language or "en"`, and the
`translate` path never updates `Document.metadata.language` to the target. `pdf_renderer.py:63`
has the same source.

**Fix.** In the translate pipeline, set `metadata.language` to the target language's BCP-47 code
(map "Spanish"→"es"). One line + a small language→code helper; both renderers already read it.

## F6 — Intentional blank/spacer pages dropped  ·  SEV: LOW · EFFORT: MED · (proposed: DEFER)
**Symptom** (Image #4): source has blank recto/verso spacer pages; translation omits them, so
pagination drifts.

**Root cause (hypothesis).** Empty blocks/chapters are collapsed during parse/render. Spine chapter
count matches source (28), so these are within-chapter empties, not dropped chapters.

**Assessment.** Blank spacer pages are print-layout artifacts with little value in a reflowable
EPUB. **Recommend deferring** unless faithful pagination is a goal. Listed for completeness.

---

## Proposed execution order (for Grok)

| # | Task | Sev | Effort | Vehicle |
|---|------|-----|--------|---------|
| F3 | `<br>` line breaks | MED | LOW | new PRP `feat--br-line-breaks.md` (quick win) |
| F5 | translated `dc:language` | LOW | LOW | fold into F3 PRP or its own tiny PRP |
| F2 | cover image + metadata | HIGH | MED | new PRP `feat--cover-preservation.md` |
| F4 | block class weight/style | MED | MED | new PRP `feat--block-class-styling.md` |
| F1 | internal links + color | HIGH | HIGH | **unpark** `feat--internal-refs.md` (Phase B) |

Quick wins first (F3+F5, F2) to bank visible fidelity fast; F4 next; F1/Phase B last as the
heaviest. Each is independently shippable and testable with a synthetic fixture **plus** a
re-run of this DMMT pass to confirm against the real book (the lesson from the red-bullets
misdiagnosis: validate fidelity fixes on the real source, not only fixtures).
