# PRP: Preserve list and quote block structure through condensation

> Product Requirement Prompt for **BreviaBook**. Operational-debt / fidelity follow-up after
> `feat--ollama-retry.md` (commit `64b163c`). Source of truth:
> [docs/ROADMAP.md](../docs/ROADMAP.md) §3 (IR block union), §7.3 (condense/synthesize).
> Operating rules: [CLAUDE.md](../CLAUDE.md).

## Goal

Fix condense + synthesize so `ListBlock` and `QuoteBlock` survive the pipeline as typed IR
blocks — not flattened to `ParagraphBlock`. A book with lists and blockquotes must come out
condensed with `type: "list"` / `type: "quote"` counts preserved (content may shrink; structure
must not).

Measured symptom (cloud run, AI Engineering EPUB → Español): **187 `list` + 8 `quote` in → 0
of each out**. Code/tables/headings/images already round-trip (49/48/476 preserved) — the bug is
specific to list/quote on the **condensation path** (parse → chunk → condense → synthesize),
not parsers or renderers.

## Why

- Lists and quotes carry reading structure (steps, bullets, citations). Losing them makes
  condensed technical books harder to scan even when the prose content is present.
- Parser → IR already models both block kinds (`epub_parser.py`); MD/EPUB renderers already
  emit them (`md_renderer.py`, `html.py`). The gap is entirely in condense reassembly.
- Translation already preserves list/quote **types** via a block plan (`translator.py`) —
  condensation should follow the same “preserve shell, transform text” idea.

## Baseline (verified at HEAD `64b163c`)

```text
$ git rev-parse --short HEAD
64b163c

$ uv run pytest --collect-only -q | tail -1
291 tests collected in 0.42s

$ uv run pytest -q | tail -1
290 passed, 1 skipped in 2.64s
```

### Block counts on fixtures (sanity anchor)

`tests/fixtures/sample.epub` (via `EpubParser`) contains at least one `QuoteBlock` and one
`ListBlock` per `tests/test_epub_parser.py`. Today, after `Condenser` + mock JSON
`{"texts": {"1": "condensed"}, ...}` on real fixture chunks, **all prose blocks become
`ParagraphBlock`** — lists/quotes from the source chunk are destroyed at reassembly even
though the parser extracted them correctly.

## Root cause (verified in code)

Three layers; only the last is the hard bug, but prompts/serialization amplify it.

### 1. Reassembly always emits `ParagraphBlock` (primary)

Both condense and synthesize share the same lossy path:

| Location | Lines | Code |
|---|---|---|
| `condenser.py` | `_reassemble` L173–175 | `for para in split_paragraphs(...): new_blocks.append(ParagraphBlock(text=para))` |
| `synthesizer.py` | `_reassemble` L283–285 | identical |

This is **by original design** — Phase 4 PRP (`004--phase-4-condenser.md`) explicitly specified:
“each `[TEXT n]` → condensed `ParagraphBlock`(s) (split on blank lines)”. Lists and quotes were
classified as condensable prose in `segment_blocks` but never rehydrated to their IR types.

### 2. LLM contract is plain strings (secondary)

`prompts.py` asks for:

```json
{"texts": {"1": "<condensed text for [TEXT 1]>", "2": "..."}}
```

No channel for list items or quote boundaries. Synthesis adds “combined result must flow as
**continuous prose**” (`build_synthesize_messages` L71) — actively discourages structure.

### 3. Serialization flattens types in the prompt body (tertiary, acceptable input)

`common.run_text` (L72–80) renders:

- `ListBlock` → markdown-ish `- item` lines (type lost in wire format)
- `QuoteBlock` → plain `block.text` (indistinguishable from paragraph in prompt)

Input flattening is fine **if** reassembly uses source block metadata — today it does not.

### What is NOT broken (do not touch)

| Stage | Evidence |
|---|---|
| EPUB parse | `test_epub_parser.py` — all block kinds including list/quote |
| Segmentation grouping | `segment_blocks` keeps `seg.blocks: list[Block]` on text runs — metadata exists but is ignored at reassembly |
| Structural preserve | `HeadingBlock`, `CodeBlock`, `TableBlock`, `ImageBlock` use `kind="keep"` / image markers |
| Render | `test_md_renderer.py::test_quote_and_list` |
| Translate | `test_translator.py` — list items translated, `ListBlock` shell kept |
| Chunker | Does not split list/quote across chunks differently from paragraphs |

## Scope

**In scope:**

- Block-aligned condense/synthesize **JSON contract** + parser/reassembler in
  `breviabook/condense/common.py`.
- Update `prompts.py` (condense + synthesize) to require structure when lists/quotes are present.
- Wire new reassembly in `condenser.py` and `synthesizer.py` (both call sites).
- Bump condense/synthesis **fingerprints** so stale checkpoint records (paragraph-only shape)
  recompute instead of silently reusing broken structure.
- Tests proving list/quote types survive **condense** and **synthesize** (mock provider, no network).
- Backward-compatible parsing when a text run contains **only** `ParagraphBlock`s (string response
  still valid — existing tests must stay green).

**Out of scope (product decision — do not touch):**

- Inline `rich` / `items_rich` fidelity (v0.2.0 styling) — condensation may continue to output
  plain `text` / `items` only; do not add rich propagation unless zero-cost.
- Block-level fidelity backlog (bullet color, cross-ref links, alignment) — separate IR design.
- Code / tables / headings / images preservation logic — already correct.
- Parser, renderer, translator, chunker, CLI changes (unless a one-line import is forced).
- Real LLM / cloud validation runs.

## Non-negotiable constraints

- [ ] Clean-room only; no code from reference repos.
- [ ] No new runtime deps; no GPL/AGPL.
- [ ] `CodeBlock` / `TableBlock` / `ImageBlock` behavior unchanged.
- [ ] Chunker must not split a list or quote across chunks (already true — keep it).
- [ ] Mock provider only in new tests; no network.
- [ ] Report exact pytest collect/pass from command output before **and** after.
- [ ] `AGENTS.md` untracked. Commit this PRP at execute start; one implementation commit with
      `Co-Authored-By: Composer <noreply@cursor.com>`.
- [ ] Production diff confined to `condense/common.py`, `condense/prompts.py`, `condense/condenser.py`,
      `condense/synthesizer.py`, and test files under `tests/`.

## Design decision (with rejected alternatives)

### Decision: **block-aligned structured JSON** + shared `parse_condensed_run()` in `common.py`

Mirror the **translator block plan** (`translator.py` L151–200): the source sequence
(`seg.blocks`) is the template; the model fills condensed **content** per block while **types
and order** are fixed.

#### Serialization (prompt body)

For each `[TEXT n]` run, emit one labeled sub-block per source block (new helper, e.g.
`serialize_run(blocks) -> str`):

```text
[TEXT 1]
[BLOCK 1 type=paragraph]
Original paragraph text…
[BLOCK 2 type=list ordered=false]
- First item
- Second item
[BLOCK 3 type=quote]
Original quote text…
```

Keep `run_text()` for backward compat or replace its call sites with `serialize_run` — either
way, condenser + synthesizer must use the labeled form.

#### LLM response shape

When a run contains **any** `ListBlock` or `QuoteBlock`, require a **JSON array** aligned 1:1
with the labeled blocks:

```json
{
  "texts": {
    "1": [
      {"type": "paragraph", "text": "Condensed intro."},
      {"type": "list", "items": ["Condensed first.", "Condensed second."], "ordered": false},
      {"type": "quote", "text": "Condensed citation."},
      {"type": "paragraph", "text": "Condensed close."}
    ]
  },
  "essential_images": []
}
```

Rules for the model (add to both condense + synthesize prompts):

- Array length and block **types** must match the `[BLOCK k type=…]` sequence.
- Condense **content** only; do not merge/split/reorder blocks.
- For lists: return condensed plain strings in `items`; preserve `ordered` from the label.
- For quotes: return condensed plain string in `text`.
- Do not emit markdown list/quote syntax inside `text` fields.

When a run contains **only** `ParagraphBlock`s, accept **either**:

- legacy string `"condensed…"` → existing `split_paragraphs` → multiple `ParagraphBlock`s, **or**
- array of `{"type": "paragraph", "text": "…"}` objects.

#### Reassembly

New `parse_condensed_run(raw: str | list, source_blocks: list[Block]) -> list[Block]` in
`common.py`:

1. If `raw` is `str` and every source block is `ParagraphBlock` → `split_paragraphs` (today).
2. If `raw` is `list` → validate len + types against `source_blocks`; build IR via
   `ParagraphBlock` / `ListBlock` / `QuoteBlock` (`ordered` from source list block).
3. On validation failure → raise `CondenseError` (existing retry / `condense_failed` paths).

#### Fallback policy when the model ignores the array contract (explicit)

**Decision:** prefer **structure-preserved-but-uncondensed** over **condensed-but-flattened**.

When a text run contains any `ListBlock` or `QuoteBlock` and the model returns a **plain string**
(instead of the required JSON array), `parse_condensed_run` raises `CondenseError`. The condenser
has **no bisection** (unlike the translator) — its only safety net is per-chunk retry +
whole-chunk passthrough:

1. Retry up to `max_retries` (default 3) with a fresh generation.
2. If all retries fail → `condense_failed=True` and the chunk is passed through **verbatim**
   (`_passthrough`: original blocks, all images kept) — structure intact, no silent flattening.

The synthesizer mirrors this at chapter level: malformed structured parse → retry → on exhaustion
keep the concatenated condensed blocks (`synthesis_failed=True`), again without flattening.

**Rejected alternative:** accept the string and run `split_paragraphs` → `ParagraphBlock` only.
That would recover `--target-ratio` on weak models but reintroduces the bug (187 lists → 0).

**Ratio trade-off:** on small/local models (e.g. default `gemma4:e4b`) that often ignore the array
contract, chunks with lists/quotes may remain uncondensed after retries. Book-level
`--target-ratio` can undershoot on list-heavy technical books — an acceptable cost until a
stronger model or a future bisection/fallback is added. We never trade structure for ratio
silently.

**Test required:** run with list/quote + string response → assert `condense_failed` and original
`ListBlock`/`QuoteBlock` types preserved (not flattened to paragraphs).

Replace the `ParagraphBlock`-only loops in `condenser._reassemble` and `synthesizer._reassemble`
with:

```python
new_blocks.extend(parse_condensed_run(texts.get(str(seg.run_id)), seg.blocks))
```

**Rich / items_rich:** out of scope — output blocks use plain `text` / `items` only
(`items_rich=None`, `rich=None`). Do not strip or mutate source during failed parse retries.

#### Fingerprint bump (checkpoint invalidation)

Add a format version field to both fingerprints so pre-fix cached chunks/chapters recompute:

- `_chunk_fingerprint` (`condenser.py`): `fp.field("condense_block_format:2")`
- `_chapter_fingerprint` (`synthesizer.py`): same token

Without this, `--resume` would reuse checkpoint payloads that are all-paragraph.

Update `tests/test_condenser.py::test_checkpoint_*` only if they assert exact hash strings
(they should not — they assert behavior). Add one test that old cached paragraph-only payload
with matching old hash is NOT required; the version bump handles invalidation.

### Rejected alternative A — treat lists/quotes as structural `kind="keep"`

Would skip condensation for those blocks (or only trim locally). **Rejected:** lists/quotes must
shrink with the rest of the prose; keeping them verbatim defeats `--target-ratio`.

### Rejected alternative B — markdown/heuristic re-parse from plain string

Ask the model to emit `- item` / `> quote` markdown; parse on reassembly. **Rejected:** synthesis
prompt already pushes “continuous prose”; heuristic parsing is brittle and untestable against
real model drift. Structured JSON matches the existing JSON-in/JSON-out contract.

### Rejected alternative C — per-block LLM calls

One provider call per block. **Rejected:** multiplies cost/latency on books with hundreds of
lists; unnecessary given `seg.blocks` already carries the template.

## Context & references

```yaml
- breviabook/ir/models.py              # ListBlock, QuoteBlock, ParagraphBlock
- breviabook/condense/common.py        # segment_blocks, run_text, split_paragraphs — extend here
- breviabook/condense/condenser.py     # _reassemble L162–184, _chunk_fingerprint
- breviabook/condense/synthesizer.py   # _reassemble L280–288, _chapter_fingerprint
- breviabook/condense/prompts.py       # JSON contract + “continuous prose” wording
- breviabook/translate/translator.py   # block plan pattern to mirror (L151–200)
- tests/test_condenser.py              # ScriptedProvider patterns, checkpoint tests
- tests/test_synthesizer.py            # synthesis reassembly tests
- tests/test_epub_parser.py            # fixture block kinds
- PRPs/004--phase-4-condenser.md       # documents the old ParagraphBlock-only reassembly
- PRPs/005--phase-5-synthesis-length-control.md
```

## Implementation blueprint

1. **Re-baseline** (report exact output):
   ```bash
   uv run pytest --collect-only -q | tail -1
   uv run pytest -q | tail -1
   ```
   Expected: `291 tests collected`, `290 passed, 1 skipped`.

2. **Commit this PRP** before coding:
   ```text
   docs(prp): track lists-quotes-condense PRP
   ```

3. **`common.py`** — add:
   - `serialize_run(blocks: list[Block]) -> str` (labeled sub-blocks).
   - `parse_condensed_run(raw: object, source_blocks: list[Block]) -> list[Block]`.
   - Small validators: `_expect_type`, list item count flexible? **Decision: item count may
     shrink but must be ≥1 when source had items; ordered flag from source, not model.**

4. **`prompts.py`** — update condense + synthesize user prompts:
   - Document array format when `[BLOCK … type=list|quote]` appears.
   - Replace “continuous prose” with “coherent flow **within each block**; preserve block types”.
   - Keep JSON-only response rule.

5. **`condenser.py` / `synthesizer.py`**:
   - `_serialize` / `_serialize(segments)`: use `serialize_run(seg.blocks)` instead of `run_text`.
   - Reassembly: `parse_condensed_run`.
   - Fingerprint version field as above.
   - `_parse_response` / `_parse_texts`: pass through array values (today they coerce to `str`
     only — extend to accept `list` values in the texts dict).

6. **Tests** (mock only):

   **`tests/test_condenser.py`** (or new `tests/test_condense_structure.py` if cleaner):

   ```python
   async def test_condense_preserves_list_and_quote_structure() -> None:
       chunk = _chunk([
           ParagraphBlock(text="Intro with filler words."),
           ListBlock(items=["First point detailed.", "Second point detailed."], ordered=False),
           QuoteBlock(text="An important citation from the author."),
           ParagraphBlock(text="Closing with filler."),
       ])
       reply = json.dumps({
           "texts": {"1": [
               {"type": "paragraph", "text": "Short intro."},
               {"type": "list", "items": ["First point.", "Second point."], "ordered": False},
               {"type": "quote", "text": "Important citation."},
               {"type": "paragraph", "text": "Short close."},
           ]},
           "essential_images": [],
       })
       cc = await Condenser(ScriptedProvider(reply), "m").condense_chunk(chunk)
       assert [b.type for b in cc.blocks] == ["paragraph", "list", "quote", "paragraph"]
       lst = cc.blocks[1]
       assert isinstance(lst, ListBlock) and lst.items == ["First point.", "Second point."]
       assert isinstance(cc.blocks[2], QuoteBlock)
   ```

   Add:
   - `test_condense_paragraph_only_string_response_still_works` (regression — existing string JSON).
   - `test_condense_structured_run_string_response_passthrough_not_flattened` — chunk with
     list+quote, provider always returns `{"texts": {"1": "flat prose string"}}` → assert
     `condense_failed is True`, original block types preserved, no silent `ParagraphBlock`-only
     flattening of the list/quote.
   - `test_condense_mismatched_array_raises_then_retries` (optional, via FlakyProvider).
   - `test_synthesizer_preserves_list_and_quote_structure` in `tests/test_synthesizer.py`
     (two-chunk chapter with list+quote in condensed input, mock array response).

   Optional unit tests on `parse_condensed_run` / `serialize_run` directly (fast, no provider).

7. **Validate** five gates. Re-run collect-only + pytest; collected count rises; all prior tests
   green.

8. **Single implementation commit**:
   ```text
   feat(condense): preserve list and quote blocks through condense/synthesize

   Co-Authored-By: Composer <noreply@cursor.com>
   ```

### New / changed files

| Path | Change |
|---|---|
| `breviabook/condense/common.py` | `serialize_run`, `parse_condensed_run`; extend `_parse` helpers if needed |
| `breviabook/condense/prompts.py` | structured JSON contract + wording |
| `breviabook/condense/condenser.py` | serialize + reassemble + fingerprint bump + parse texts |
| `breviabook/condense/synthesizer.py` | same |
| `tests/test_condenser.py` and/or `tests/test_condense_structure.py` | structure preservation |
| `tests/test_synthesizer.py` | synthesis path preserves types |
| `PRPs/feat--lists-quotes-condense.md` | this file |

## Validation gates (must all pass)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict breviabook
uv run pytest -q
uv run pip-licenses --partial-match --fail-on "General Public License;GPL" --ignore-packages pyphen
```

### Feature-specific checks

- [ ] `rg 'ParagraphBlock\(text=para\)' breviabook/condense/` → **no matches** in reassembly paths
      (parsing centralized in `parse_condensed_run`).
- [ ] `parse_condensed_run` used from both `condenser._reassemble` and `synthesizer._reassemble`.
- [ ] Fingerprints include `condense_block_format:2` (or equivalent version token).
- [ ] Before/after from **command output**:
  - collect-only count = **291 + N** new tests.
  - pytest summary = **290 + N passed**, same `1 skipped`, zero failures.
- [ ] Existing `test_condenser.py` paragraph/code/image/checkpoint tests still green.
- [ ] New test: condense + synthesize both preserve `list` / `quote` block types.
- [ ] New test: structured run + string response → passthrough / `condense_failed`, not flattened.
- [ ] No real LLM calls in tests.

## Acceptance criteria

- [ ] Condensed output retains `ListBlock` and `QuoteBlock` where the source had them (types + order).
- [ ] Paragraph-only runs still accept legacy string `texts` responses.
- [ ] Code/tables/headings/images unchanged behavior.
- [ ] Stale checkpoints invalidated via fingerprint bump.
- [ ] Fallback policy documented and tested: string on structured run → retry → passthrough, never
      silent flatten.
- [ ] Five quality gates green; pytest counts reported from command output.
- [ ] PRP committed at execute start; one implementation commit with Composer co-author trailer;
      `AGENTS.md` still untracked.

## Confidence score

8/10 — Root cause is a single, well-localized reassembly bug duplicated in two modules; fix is
mechanical once the JSON contract is defined. Residual risk: real models may emit malformed arrays
(mitigated by existing retry + `condense_failed` / synthesis passthrough) and synthesis trim
passes re-segmenting already-flattened checkpoint data until recomputed with the new fingerprint.
