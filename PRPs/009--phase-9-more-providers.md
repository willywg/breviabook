# PRP: Phase 9 — More providers + key rotation

> Product Requirement Prompt for **BreviaBook**. Source of truth: [docs/ROADMAP.md](../docs/ROADMAP.md) §7.4, §6, §9, §10 (Phase 9), §12, §14.
> Operating rules: [CLAUDE.md](../CLAUDE.md). Builds on PRP 000 (LLM layer).

## Goal

Add OpenAI (+ OpenAI-compatible via `--api-endpoint`/base_url), Gemini, and OpenRouter
providers behind the existing `LLMProvider` Protocol, all via litellm, with **API-key rotation
+ failover** on auth/rate-limit errors. No model code is copied from TBL (clean-room, §14).

## Why

- Frees BreviaBook from local-only Ollama: paid APIs when they make sense, and any
  OpenAI-compatible endpoint (vLLM/LM Studio/LocalAI) via base_url.
- Key rotation keeps long jobs alive across rate limits / dead keys.

## Scope

**In scope:**
- `llm/key_pool.py`: `KeyPool` (round-robin over comma-separated keys).
- `llm/rate_limit.py`: error classification (`is_rate_limit_error`/`is_auth_error`) +
  `with_key_rotation` retry/rotate helper (injectable sleep → testable).
- `llm/providers/litellm_base.py`: `LiteLLMProvider` base (route prefix + pool + base_url +
  rotation). Lazy-imports litellm; accepts an injectable `completer` for tests.
- `llm/providers/{openai,gemini,openrouter}.py`: thin subclasses.
- `llm/factory.py`: wire the new providers; build pools from `settings.keys_for(...)`;
  require keys (except OpenAI-compatible local endpoints); accept `api_endpoint` (base_url).
- CLI: pass `--api-endpoint` to the factory (drop the ollama-only mutation hack).
- Tests with injected completers/errors (no network, no litellm import needed).

**Out of scope:** `generate_with_image` / vision (Phase 11), translation (Phase 10).

## Non-negotiable constraints (CLAUDE.md / ROADMAP §12, §14)

- [ ] Clean-room: rotation/failover reimplemented from this spec, not copied from TBL.
- [ ] No GPL/AGPL deps (litellm MIT only).
- [ ] **No cross-provider key leakage:** only OpenAI accepts a custom `--api-endpoint`;
      Gemini/OpenRouter keys go only to their fixed hosts. A provider only ever sends its own
      pool's key.
- [ ] Nothing outside `llm/` imports litellm; lazy-imported so tests/CLI stay fast.
- [ ] Missing keys fail with a clear, actionable error (which env var to set).

## Context & references

```yaml
- breviabook/llm/base.py        # LLMProvider Protocol, Message
- breviabook/llm/factory.py     # get_provider(name, settings)
- breviabook/llm/providers/ollama.py  # existing litellm usage pattern
- breviabook/config.py          # keys_for(provider) -> comma-split list
- litellm routes: openai/<m> (+api_base for compatible), gemini/<m>, openrouter/<m>
# Study (never copy): TBL key_pool.py / rate_limit_handler.py
```

## Design

- `KeyPool(keys)`: filters empties; `current`, `rotate()`, `__len__`, `__bool__`.
- `with_key_rotation(pool, call, *, max_retries, sleep)`: try `call(pool.current)`; on auth
  error rotate to the next key (up to len-1 times); on rate-limit rotate + backoff-sleep (up
  to max_retries); other errors propagate. Error class matched by MRO name so litellm's
  exceptions and test doubles both work.
- `LiteLLMProvider(name, route, pool, base_url=None, completer=None)`: `generate` wraps the
  litellm call in `with_key_rotation`; `_acompletion` lazy-imports litellm unless a `completer`
  is injected.
- Factory: ollama (endpoint), openai (pool; base_url=api_endpoint; local endpoint may skip the
  key via an "EMPTY" placeholder), gemini, openrouter.

## Implementation blueprint

1. `llm/key_pool.py`, `llm/rate_limit.py`.
2. `llm/providers/litellm_base.py` + `openai.py`/`gemini.py`/`openrouter.py`.
3. `llm/factory.py` (wire + key checks + api_endpoint); `cli.py` (pass api_endpoint).
4. Tests: `tests/test_key_pool.py`, `tests/test_rate_limit.py`, `tests/test_providers.py`,
   extend `tests/test_llm_factory.py`.

### New / changed files

- `breviabook/llm/key_pool.py`, `breviabook/llm/rate_limit.py`
- `breviabook/llm/providers/litellm_base.py`, `.../openai.py`, `.../gemini.py`, `.../openrouter.py`
- `breviabook/llm/factory.py`, `breviabook/cli.py`
- tests as above

## Validation gates (must all pass)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict breviabook
uv run pytest -q
uv run pip-licenses --fail-on "GPL"
```

### Feature-specific checks

- [ ] `KeyPool` round-robins; empty pool is falsy.
- [ ] `with_key_rotation`: success first try; auth error rotates to next key then succeeds;
      rate-limit rotates + sleeps (injected) then succeeds; bounded; non-retryable propagates.
- [ ] `LiteLLMProvider.generate` (injected completer) sends `route/model` + the pool key and
      returns the content; rotates the key after a rate-limit error.
- [ ] Factory returns the right provider per name; missing key → clear error; OpenAI with
      `api_endpoint` and no key works (local).
- [ ] Nothing outside `llm/` imports litellm; existing ollama tests still pass.

## Acceptance criteria

- [ ] OpenAI/Gemini/OpenRouter selectable via `--provider`; OpenAI-compatible via `--api-endpoint`.
- [ ] Key rotation/failover works and is bounded.
- [ ] All five validation gates green.

## Confidence score

8/10 — Thin litellm wrappers; the real logic (pool + rotation) is pure and fully unit-tested
with injected doubles.
