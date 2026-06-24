# PRP: {FEATURE_NAME}

> Product Requirement Prompt for **Brevia**. Goal: enough context + executable
> validation for one-pass implementation. Source of truth: [docs/ROADMAP.md](../../docs/ROADMAP.md).
> Operating rules: [CLAUDE.md](../../CLAUDE.md).

## Goal

{What end state this PRP delivers. Reference the ROADMAP phase, e.g. "Phase N — ...".}

## Why

- {User value / how it advances the pipeline (parse → chunk → condense → synthesize → translate → render).}
- {What it unblocks downstream.}

## Scope

**In scope:** {bullet list}
**Out of scope:** {bullet list — defer to later phases}

## Non-negotiable constraints (from CLAUDE.md / ROADMAP §14)

- [ ] Clean-room: no code copied/translated from reference repos.
- [ ] No GPL/AGPL runtime deps (no `ebooklib`, no `PyMuPDF`). Permissive libs only.
- [ ] `code` and `image` blocks are never summarized or split.
- [ ] No secrets or job DBs committed; respect zip-slip / SSRF guards where relevant.

## Context & references

```yaml
# Files to read/follow in this repo:
- docs/ROADMAP.md          # spec — read the relevant § for this phase
- CLAUDE.md                # operating rules, stack, license rules
- brevia/ir/models.py      # the IR — Document/Chapter/Block/ImageAsset (once it exists)

# External docs (add concrete URLs as needed):
- {library}: {url}
```

Patterns to study (NEVER copy code): {TBL path / cognitivetech / OllamaBook-Summarize, per ROADMAP §2}

## Implementation blueprint

{Ordered, concrete steps. Reference real files under brevia/ from ROADMAP §6.}

1. {step}
2. {step}
3. {step}

### New / changed files

- `brevia/{...}.py` — {purpose}
- `tests/{...}.py` — {what it asserts}

## Validation gates (must all pass)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict brevia
uv run pytest -q
uv run pip-licenses --fail-on "GPL"
```

### Feature-specific checks

- [ ] {e.g. "round-trip: parse fixture EPUB → render MD preserves all blocks + images"}
- [ ] {e.g. "chunker never splits a code block; chunk size within range"}
- [ ] {test uses the deterministic mock provider, no real LLM call}

## Acceptance criteria

- [ ] {observable outcome 1}
- [ ] {observable outcome 2}
- [ ] All validation gates green.

## Confidence score

{1-10} — {one line on remaining risk}
