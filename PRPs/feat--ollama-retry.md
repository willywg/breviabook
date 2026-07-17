# PRP: Retry/backoff on `OllamaProvider`

> Product Requirement Prompt for **BreviaBook**. Operational-debt follow-up — last item from
> the architecture audit queue after `feat--usage-protocol.md` (commit `2038a64`).
> Source of truth: [docs/ROADMAP.md](../docs/ROADMAP.md) §7.4 (rate-limit handling), §14.
> Operating rules: [CLAUDE.md](../CLAUDE.md).

## Goal

Add bounded exponential backoff retries to `OllamaProvider.generate` and
`OllamaProvider.generate_with_image` when litellm raises transient errors (`RateLimitError`,
`Timeout`, `APIConnectionError` — typical when Ollama is loading a model or temporarily busy).
Reuse the existing error classification in `rate_limit.py`; do **not** rotate API keys (Ollama
is local and keyless).

## Why

- Today both Ollama entry points call `litellm.acompletion` **once** with no recovery
  (`breviabook/llm/providers/ollama.py:35–40`, `:53–58`). A single `APIConnectionError` or
  `Timeout` kills the entire condense/translate job.
- Cloud providers already survive the same failures via `with_key_rotation` in
  `litellm_base.py:90`. Ollama was left out when Phase 9 added key rotation — it needs the
  **backoff half** of that pattern, not the key-rotation half.
- This closes the last operational-debt item on `main`; after it, the audit queue is empty.

## Baseline (verified at HEAD `2038a64`)

```text
$ git rev-parse --short HEAD
2038a64

$ uv run pytest --collect-only -q | tail -1
282 tests collected in 1.83s

$ uv run pytest -q | tail -1
281 passed, 1 skipped in 2.31s
```

### Ollama today — no retry, no test injection

| Method | Lines | Behavior |
|---|---|---|
| `generate` | `ollama.py:28–43` | single `litellm.acompletion`; usage + content extract |
| `generate_with_image` | `ollama.py:45–61` | same, with vision message parts |

No `max_retries`, no `completer`, no `sleep` hook — unlike `LiteLLMProvider`
(`litellm_base.py:53–54`, `:67–72`, `:90`).

### Existing retry infra (`rate_limit.py`)

| Symbol | Role |
|---|---|
| `is_rate_limit_error(exc)` | MRO name match: `RateLimitError`, `Timeout`, `APIConnectionError` |
| `with_key_rotation(...)` | auth rotate + rate-limit rotate **and** backoff (`backoff_base * 2**attempt`) |
| Defaults | `max_retries=3`, `backoff_base=0.5`, `sleep=asyncio.sleep` |

`with_key_rotation` always calls `pool.rotate()` on rate-limit — meaningless for Ollama.

## Scope

**In scope:**

- New keyless helper `retry_with_backoff` in `breviabook/llm/rate_limit.py` reusing
  `is_rate_limit_error` (default retry predicate).
- Wire `OllamaProvider` through the helper for **both** `generate` and `generate_with_image`.
- Constructor knobs on `OllamaProvider`: `max_retries: int = 3` (match `LiteLLMProvider`),
  injectable `sleep` (default `asyncio.sleep`) and optional `completer` (mirror
  `litellm_base.py` — keeps tests off the network and off litellm import).
- New `tests/test_ollama.py`: unit tests for `retry_with_backoff` + provider-level tests with
  injected completer/sleep (pattern from `tests/test_rate_limit.py` and `tests/test_providers.py`).

**Out of scope:**

- Changing `with_key_rotation` behavior or refactoring it to call `retry_with_backoff` internally
  (same file is allowed, but existing `tests/test_rate_limit.py` must stay green unchanged —
  refactor only if provably behavior-identical; **default: leave `with_key_rotation` as-is**).
- Factory / CLI changes (`get_provider("ollama", ...)` signature stays endpoint-only unless
  a future PR exposes `--max-retries` globally).
- Retry on cloud providers beyond what `with_key_rotation` already does.
- New runtime dependencies (`tenacity`, etc.).

## Non-negotiable constraints

- [ ] Clean-room only; no code from reference repos.
- [ ] No new runtime deps; no GPL/AGPL.
- [ ] IR / pipeline invariants untouched.
- [ ] Transient errors retry; **logic errors propagate immediately** (no retry on unknown
      exception types).
- [ ] `sleep` injectable for deterministic tests (same contract as `with_key_rotation`).
- [ ] All tests mock/inject — **never** call a real Ollama server or the public internet.
- [ ] Production diff confined to `breviabook/llm/rate_limit.py`, `breviabook/llm/providers/ollama.py`,
      and new `tests/test_ollama.py`.
- [ ] Report exact pytest collect/pass counts from command output before **and** after.
- [ ] Do not commit `AGENTS.md`. Commit this PRP at execute start; one implementation commit
      with `Co-Authored-By: Composer <noreply@cursor.com>` trailer.

## Design decision (with rejected alternative)

### Decision: **(a)** extract `retry_with_backoff` in `rate_limit.py`; Ollama uses it directly

Add a keyless async helper:

```python
async def retry_with_backoff(
    call: Callable[[], Awaitable[T]],
    *,
    max_retries: int = 3,
    backoff_base: float = 0.5,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    is_retryable: Callable[[BaseException], bool] = is_rate_limit_error,
) -> T:
    ...
```

Loop: try `call()`; on retryable error and `attempts < max_retries`, `await sleep(backoff_base * 2**attempts)`,
increment, continue; otherwise re-raise. Same backoff formula as `with_key_rotation` L56.

**Ollama** mirrors `LiteLLMProvider._run` shape:

1. `_acompletion(**kwargs)` — lazy litellm or injected `completer`.
2. `_run(messages, model, **opts) -> str` — build inner `call()` that completes, updates
   `self.usage`, returns text; wrap with `retry_with_backoff(call, max_retries=..., sleep=...)`.
3. `generate` / `generate_with_image` delegate to `_run` (vision message assembly stays in
   `generate_with_image`).

Store `self.max_retries` and `self._sleep` from `__init__` (defaults `3` and `asyncio.sleep`).

**Why not refactor `with_key_rotation` to call `retry_with_backoff`?**

- Rate-limit path in `with_key_rotation` also **rotates the key pool** before sleeping. A naive
  extraction would either drop rotation (regression for cloud providers) or nest helpers awkwardly.
- Leaving `with_key_rotation` untouched keeps `tests/test_rate_limit.py` as a frozen contract;
  Ollama and cloud share `is_rate_limit_error` + backoff constants, not necessarily one loop.

**Rejected alternative (b): inline retry loop only inside `OllamaProvider`**

- Duplicates the backoff formula and retry predicate wiring already centralized in
  `rate_limit.py`. Works, but the next provider without keys would copy-paste again. Helper (a) is
  ~15 lines once, testable in isolation, and matches the ROADMAP’s “rate_limit handler” layer.

## Context & references

```yaml
- breviabook/llm/rate_limit.py           # is_rate_limit_error, with_key_rotation — add helper
- breviabook/llm/providers/ollama.py     # target: wrap acompletion with retry
- breviabook/llm/providers/litellm_base.py  # mirror: _acompletion, _run, max_retries, completer
- tests/test_rate_limit.py               # backoff/sleep test patterns to copy
- tests/test_providers.py                # completer injection + RateLimitError double
- tests/test_llm_factory.py              # factory smoke — must stay green, no edits expected
- PRPs/009--phase-9-more-providers.md    # original with_key_rotation spec
- PRPs/feat--usage-protocol.md           # immediately prior debt item; Ollama usage unchanged
```

## Implementation blueprint

1. **Re-baseline** (report exact command output):
   ```bash
   uv run pytest --collect-only -q | tail -1
   uv run pytest -q | tail -1
   ```
   Expected: `282 tests collected`, `281 passed, 1 skipped`.

2. **Commit this PRP** before coding:
   ```text
   docs(prp): track ollama-retry PRP
   ```

3. **`rate_limit.py`** — add `retry_with_backoff`:
   - Generic type var `T` for return type.
   - `call` takes **no** key argument (keyless).
   - Default `is_retryable=is_rate_limit_error`.
   - Document that `with_key_rotation` is the keyed variant; this is the keyless backoff loop.
   - Do **not** change `with_key_rotation` body unless a behavior-identical refactor falls out
     naturally (not required).

4. **`ollama.py`** — structural parity with `litellm_base` (minus pool/keys):
   ```python
   def __init__(
       self,
       endpoint: str = "http://localhost:11434",
       *,
       max_retries: int = 3,
       completer: Completer | None = None,
       sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
   ) -> None: ...
   ```
   - `_acompletion`: completer or lazy `litellm.acompletion`.
   - `_run`: `retry_with_backoff` around usage + content extraction.
   - `generate` / `generate_with_image`: unchanged public signatures; both route through `_run`.

5. **`tests/test_ollama.py`** — new file, all mock, no network:

   **Helper-level** (local exception classes, same names as `test_rate_limit.py`):
   - `test_retry_success_first_try` — one call, no sleep.
   - `test_retry_backoff_on_connection_error` — fails once with `APIConnectionError`, then OK;
     assert sleep called with `0.5` (first backoff).
   - `test_retry_exponential_backoff` — fail twice, assert sleeps `[0.5, 1.0]`.
   - `test_retry_bounded_by_max_retries` — always fails; `max_retries=2` ⇒ 3 total attempts then raise.
   - `test_retry_non_retryable_propagates` — `OtherError` ⇒ single attempt, no sleep.

   **Provider-level** (injected `completer` + `sleep` on `OllamaProvider`):
   - `test_ollama_generate_retries_then_succeeds` — completer raises `APIConnectionError` once.
   - `test_ollama_generate_with_image_retries` — same for vision path.
   - `test_ollama_non_retryable_fails_fast` — completer raises generic error; one call.
   - Optional smoke: `test_ollama_success_first_try` — happy path content + `api_base` forwarded.

   Use `_response(text)` helper like `test_providers.py`. Exception classes defined locally
   (MRO name match — no litellm import).

6. **Validate** all five gates. Re-run collect-only and pytest; collected count must **increase**
   (new tests only); all prior tests still pass.

7. **Single implementation commit**:
   ```text
   feat(llm): add retry/backoff to OllamaProvider

   Co-Authored-By: Composer <noreply@cursor.com>
   ```

### New / changed files

| Path | Change |
|---|---|
| `breviabook/llm/rate_limit.py` | add `retry_with_backoff` |
| `breviabook/llm/providers/ollama.py` | `_acompletion`, `_run`, retry wiring; `max_retries`, `completer`, `sleep` |
| `tests/test_ollama.py` | **new** — helper + provider retry tests |
| `PRPs/feat--ollama-retry.md` | this file (committed at execute start) |

No changes to `factory.py`, `cli.py`, `tests/test_rate_limit.py`, or other providers.

## Validation gates (must all pass)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict breviabook
uv run pytest -q
uv run pip-licenses --partial-match --fail-on "General Public License;GPL" --ignore-packages pyphen
```

### Feature-specific checks

- [ ] `rg -n 'litellm\.acompletion' breviabook/llm/providers/ollama.py` hits only `_acompletion`
      (not bare in `generate` / `generate_with_image`).
- [ ] Both public methods route through `_run` → `retry_with_backoff`.
- [ ] `is_rate_limit_error` reused (not duplicated error-name sets in `ollama.py`).
- [ ] Before/after from **command output**:
  - `uv run pytest --collect-only -q | tail -1` → **282 + N** (N = new tests in `test_ollama.py`).
  - `uv run pytest -q` → **281 + N passed**, same `1 skipped`, zero failures.
- [ ] `tests/test_rate_limit.py` and `tests/test_llm_factory.py` unchanged and green.
- [ ] No real LLM / Ollama / network in new tests.

## Acceptance criteria

- [ ] Transient litellm errors on Ollama (`RateLimitError`, `Timeout`, `APIConnectionError`) retry
      with exponential backoff up to `max_retries` (default 3).
- [ ] Non-retryable errors propagate on the first failure.
- [ ] `generate` **and** `generate_with_image` both retry.
- [ ] `sleep` injectable; tests assert backoff intervals without real delays.
- [ ] `retry_with_backoff` lives in `rate_limit.py` and is covered by tests.
- [ ] Five quality gates green; pytest counts reported from command output.
- [ ] PRP committed at execute start; one implementation commit with Composer co-author trailer;
      `AGENTS.md` remains untracked.

## Confidence score

9/10 — Small, well-scoped change with an existing pattern (`litellm_base` + `test_rate_limit`).
Residual risk: typing the generic `retry_with_backoff` under `mypy --strict`, and ensuring
`usage.add` runs only on successful completion (not on intermediate failures) — match
`litellm_base._run` which also accumulates usage only after a successful response.
