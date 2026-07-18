# PRP: Preserve `<br>` in rich + set translated `dc:language` (F3 + F5)

> Product Requirement Prompt for **BreviaBook**. Fidelity QA sprint
> ([docs/fidelity-qa-2026-07.md](../docs/fidelity-qa-2026-07.md) F3 + F5).
> Source of truth: [docs/ROADMAP.md](../docs/ROADMAP.md). Operating rules: [CLAUDE.md](../CLAUDE.md).

## Goal

1. **F3** — Intra-paragraph line breaks (`<br>`) survive parse → IR `rich` → EPUB/PDF/Markdown,
   without changing the plain `text` projection that feeds condensation.
2. **F5** — After translation, `Document.metadata.language` is the BCP-47 code of the target
   language so EPUB `dc:language` and PDF `<html lang>` are correct (e.g. Spanish → `es`).

## Why

- **F3 root cause** (`htmlsan.py:221-222`): `_render_child` maps `<br>` → `" "`, so credits and
  similar multi-line paragraphs collapse into one flowing blob.
- **F5 root cause** (`epub_renderer.py:118`, `pdf_renderer.py:63`): renderers already read
  `meta.language or "en"`, but the translate path never updates `Document.metadata.language`.

## Scope

**In scope:**
- `htmlsan`: emit `<br/>` in sanitized `rich`; `strip_tags` maps `<br>` → space (plain `text` unchanged).
- Markdown renderer: `<br>` → GFM hard break (`  \n`).
- EPUB/PDF: emit `rich` verbatim (already via `html._inline`) — no special case beyond allowlist.
- Translator prompt: mention `<br>` among preservable tags.
- `to_bcp47(lang)` helper + set `metadata.language` in `Translator.translate_document`.
- Synthetic fixture tests for both.

**Out of scope:**
- F1 (internal links / `_SAFE_LINK_SCHEMES`) — do not touch.
- F2 (cover), F4 (block class weight/style).
- Changing condensation prompts or chunk boundaries.

## Non-negotiable constraints (CLAUDE.md / ROADMAP §14)

- [ ] Clean-room; no GPL/AGPL deps.
- [ ] Do **not** modify `htmlsan._SAFE_LINK_SCHEMES` or the href gate.
- [ ] Invariant: when `rich` is set, `text == strip_tags(rich)` (with `<br>` → space in strip).
- [ ] Tests use mock provider only; no real LLM.

## Context & references

```yaml
- docs/fidelity-qa-2026-07.md   # F3, F5
- breviabook/utils/htmlsan.py   # _render_child br → " "; strip_tags; sanitize_inline
- breviabook/render/html.py     # _inline emits rich verbatim
- breviabook/render/md_renderer.py  # _node_md — add br → "  \n"
- breviabook/translate/translator.py  # translate_document returns Document(metadata=…)
- breviabook/render/epub_renderer.py:118  # lang = meta.language or "en"
- breviabook/render/pdf_renderer.py:63    # html lang=
- tests/test_htmlsan.py
- tests/test_translator.py
```

## Implementation blueprint

### F3 — `<br/>` in rich

1. In `_render_child`, when `name == "br"`, return `"<br/>"` (not `" "`).
2. Update module docstring allowlist to include `<br/>`.
3. In `strip_tags`, replace every `<br>` with a space before `get_text()` so
   `"Editor<br/>Project"` → `"Editor Project"` (condensation `text` unchanged in spirit).
4. In `md_renderer._node_md`, handle `br` → `"  \n"`.
5. In translator system/user rules, add `<br>` to the list of tags to preserve.
6. Tests:
   - `sanitize_inline("A<br/>B") == "A<br/>B"`
   - `strip_tags("A<br/>B") == "A B"`
   - HTML `block_to_html` keeps `<br/>` in output
   - MD renderer emits a hard line break
   - Parser fixture: `<p>Editor<br/>Project</p>` → rich has `<br/>`, text is `"Editor Project"`

### F5 — language code

1. Add `breviabook/utils/langcodes.py` with `to_bcp47(name: str) -> str`:
   - Map common English (and a few localized) language names → ISO 639-1 (`Spanish`→`es`).
   - If input already looks like BCP-47 (`es`, `es-MX`, `zh-Hans`), normalize and return.
   - Unknown names: best-effort lowercase primary subtag only when already code-shaped; else a
     documented fallback (prefer returning a lowercased 2–3 letter code from a name table; do
     not invent wrong codes — fall back to `"und"` only when totally unknown).
2. In `Translator.translate_document`, after translating chapters:
   ```python
   meta = doc.metadata.model_copy(update={"language": to_bcp47(self.target_lang)})
   return Document(metadata=meta, images=doc.images, chapters=list(chapters))
   ```
3. Test: translate a tiny doc with mock provider `target_lang="Spanish"` →
   `metadata.language == "es"`; render OPF contains `<dc:language>es</dc:language>`.

### New / changed files

- `breviabook/utils/htmlsan.py` — br preservation + strip_tags
- `breviabook/utils/langcodes.py` — new helper
- `breviabook/render/md_renderer.py` — br → hard break
- `breviabook/translate/translator.py` — set language; prompt mention br
- `tests/test_htmlsan.py` / `tests/test_br_and_language.py` — fixtures
- `tests/test_translator.py` — language update assertion

## Validation gates (must all pass)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict breviabook
uv run pytest
uv run pip-licenses --partial-match --fail-on "General Public License;GPL" --ignore-packages pyphen
```

### Feature-specific checks

- [ ] `_SAFE_LINK_SCHEMES` and href gate unchanged
- [ ] `text == strip_tags(rich)` holds for br-bearing rich
- [ ] Mock provider only

## Acceptance criteria

- [ ] Credits-style `<p>A<br/>B<br/>C</p>` keeps line breaks in EPUB/PDF HTML output
- [ ] Plain `text` for that paragraph is space-joined (no behavioural change for condense)
- [ ] Translated Spanish EPUB OPF has `dc:language` = `es`
- [ ] All validation gates green

## Confidence score

8/10 — localized changes; main risk is whitespace collapse around `<br/>` in `sanitize_inline`'s
final `_WS_RE.sub` (should be fine since tags are not whitespace).
