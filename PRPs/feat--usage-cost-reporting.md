# PRP: Usage & cost reporting

> Product Requirement Prompt for **BreviaBook**. Cross-cutting feature (relates to ROADMAP §10 Phase 12).
> Operating rules: [CLAUDE.md](../CLAUDE.md). Builds on PRP 009 (providers).

## Goal

Capture real token usage (prompt / completion / cached) and approximate cost per run by
reading each LLM response's `usage`, accumulating it on the provider, and reporting a
usage/cost summary at the end of `breviabook condense`.

## Why

- After a real (paid) run the owner wants to see what it cost and how many tokens flowed,
  including cache hits — there's no visibility today.

## Scope

**In scope:**
- `llm/usage.py`: `Usage` accumulator (prompt/completion/cached tokens, calls, cost_usd) +
  `extract_usage(response)` (dict- and litellm-object-safe).
- `LiteLLMProvider` and `OllamaProvider` accumulate `self.usage` per call; cost via
  `litellm.completion_cost` best-effort on the real path (0 for local/ollama and for tests).
- `pipeline.condense_book` surfaces the provider's usage in `CondenseResult.usage`.
- CLI prints a usage/cost table after a run.
- Tests (injected completer with a `usage` block; no network).

**Out of scope:** the `--dry-run` *pre*-estimate cost (Phase 12); per-step breakdown.

## Non-negotiable constraints (CLAUDE.md)

- [ ] No new runtime deps; litellm stays lazy-imported (cost computed only on the real path).
- [ ] Usage tracking must not break providers that don't track it (pipeline reads defensively).
- [ ] Token counts come from the model response, not re-estimated.

## Design

- `Usage.add(prompt, completion, cached, cost)` increments totals + `calls`.
- Providers hold `self.usage = Usage()`; after each successful completion, add the response's
  usage. `cost_usd` summed from `litellm.completion_cost` (unknown/preview models → 0.0).
- `CondenseResult.usage: Usage | None`; pipeline sets it from `getattr(provider, "usage", None)`.
- CLI: if `usage.calls`, print prompt/completion/cached/total tokens, calls, and `~$cost`
  (note "0" when the model isn't in litellm's price map).

## Implementation blueprint

1. `breviabook/llm/usage.py`.
2. `litellm_base.py` + `ollama.py`: accumulate usage.
3. `pipeline.py`: `CondenseResult.usage` + populate.
4. `cli.py`: usage/cost table.
5. Tests: `tests/test_usage.py` + provider/pipeline usage assertions.

## Validation gates (must all pass)

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict breviabook
uv run pytest -q && uv run pip-licenses --fail-on "GPL"
```

### Feature-specific checks

- [ ] `extract_usage` reads prompt/completion/cached from a dict response.
- [ ] A provider accumulates usage across multiple `generate` calls.
- [ ] `condense_book` returns a populated `usage` when the provider tracks it; defaults to None
      for providers that don't.
- [ ] CLI prints the usage table after a real run (covered by a smoke assertion on build path).

## Confidence score

8/10 — Mostly plumbing; the only fuzziness is cost for preview models not in litellm's price
map (reported as ~$0, clearly labeled).
