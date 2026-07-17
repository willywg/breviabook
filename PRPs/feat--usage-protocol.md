# PRP: Move `usage` onto the `LLMProvider` Protocol

> Product Requirement Prompt for **BreviaBook**. Operational-debt follow-up after
> `feat--refactor-condense-book.md` (commit `805de19` / PRP track `7dc4c79`).
> Source of truth: [docs/ROADMAP.md](../docs/ROADMAP.md) §7.4, §14.
> Operating rules: [CLAUDE.md](../CLAUDE.md).
> Supersedes the "providers that don't track usage" escape hatch from
> [PRPs/feat--usage-cost-reporting.md](feat--usage-cost-reporting.md).

## Goal

Declare `usage: Usage` on the `@runtime_checkable` `LLMProvider` Protocol so every
provider — production and test double — exposes a real accumulator. Replace the three
defensive `getattr(..., "usage", None)` call sites with direct attribute access, and drop
the `isinstance(usage, Usage)` guard in `pipeline.py` once the type is guaranteed.

## Why

- Both production providers already own `self.usage = Usage()`
  (`litellm_base.py:62`, `ollama.py:26`). The contract just never said so.
- The three `getattr` sites exist only because the Protocol is incomplete. That is the last
  remaining piece of the original usage-reporting feature's defensive design
  (`feat--usage-cost-reporting.md`: "must not break providers that don't track it").
- With usage on the Protocol, `mypy --strict` can type `provider.usage` / `llm.usage`
  without `Any` or casts, and `isinstance(x, LLMProvider)` at runtime correctly rejects
  objects that omit it.

## Baseline (verified at HEAD `7dc4c79`)

```text
$ git rev-parse --short HEAD
7dc4c79

$ uv run pytest --collect-only -q | tail -1
282 tests collected in 1.75s

$ uv run pytest -q | tail -1
281 passed, 1 skipped in 2.37s
```

### `getattr` sites (production — exactly 3)

| File | Line | Code today |
|---|---|---|
| `breviabook/pipeline.py` | 326 | `usage = getattr(provider, "usage", None)` |
| `breviabook/cli.py` | 276 | `usage_source = getattr(llm, "usage", None)` |
| `breviabook/cli.py` | 406 | `usage_source = getattr(llm, "usage", None)` |

Pipeline also has L334: `usage=usage if isinstance(usage, Usage) else None` — redundant once
the attribute is typed `Usage`.

### Production providers already define `usage`

| Provider | Location |
|---|---|
| `LiteLLMProvider` (OpenAI / Gemini / OpenRouter) | `breviabook/llm/providers/litellm_base.py:62` — `self.usage = Usage()` |
| `OllamaProvider` | `breviabook/llm/providers/ollama.py:26` — `self.usage = Usage()` |

No production provider changes required beyond the Protocol declaration.

### Protocol today (`breviabook/llm/base.py`)

```python
@runtime_checkable
class LLMProvider(Protocol):
    name: str
    async def generate(...) -> str: ...
```

`Usage` is **not** imported in `base.py` today.

## Scope

**In scope:**

- Add `usage: Usage` to `LLMProvider` in `breviabook/llm/base.py` (alongside `name: str`).
  Import `Usage` from `breviabook.llm.usage`.
- Replace the 3 `getattr` sites with `provider.usage` / `llm.usage`.
- Simplify pipeline result assembly: `usage=provider.usage` (drop `getattr` + `isinstance`).
- Update every test double that structurally implements `generate` so it declares `usage`,
  so `@runtime_checkable` and direct attribute access keep working.
- Update the one test that **asserts the old escape hatch**
  (`test_pipeline_usage_none_when_provider_untracked`) to match the new contract.

**Out of scope:**

- Retry/backoff on `OllamaProvider` (next debt item).
- Changing how providers *accumulate* usage (`Usage.add`, `extract_usage`, pricing).
- Changing the CLI usage table UI or `RunReporter` (only how `usage_source` is obtained).
- Adding `usage` to `VisionProvider` (vision is a capability mixin; ranking always receives
  an object that is also an `LLMProvider` in practice, and fakes that only implement vision
  inherit from a base that will carry `usage`).
- Making `CondenseResult.usage` non-optional (keep `Usage | None = None` for the smallest
  public-API delta; pipeline always assigns `provider.usage` after a run).

## Non-negotiable constraints

- [ ] Clean-room only; no code from reference repos.
- [ ] No new runtime deps; no GPL/AGPL.
- [ ] IR invariants untouched (`code` / `image` never summarized or split).
- [ ] `mypy --strict breviabook` green; no `# type: ignore` papering over the Protocol.
- [ ] Suite size and pass count preserved: report exact numbers from
      `uv run pytest --collect-only -q | tail -1` and `uv run pytest -q` **before and after**,
      not from memory.
- [ ] Production diff confined to `llm/base.py`, `pipeline.py`, `cli.py` + the test doubles
      (and the one assertion that encodes the obsolete escape hatch).
- [ ] Do not commit `AGENTS.md`. Commit this PRP **before or with** the implementation
      (new repo rule: never leave a PRP untracked at execute time).

## Known gotchas — test doubles (the crux)

### Why this matters

`LLMProvider` is `@runtime_checkable`. Adding a **non-method** attribute means
`isinstance(obj, LLMProvider)` uses `hasattr(obj, "usage")`. Verified locally:

```text
Plain (no usage)  isinstance → False
WithUsage         isinstance → True
```

Direct `provider.usage` in pipeline/CLI will also `AttributeError` on any fake that omits it.
Stage modules type-hint `provider: LLMProvider`; structural subtyping under mypy also
requires the attribute once it is on the Protocol (tests themselves are not under
`mypy --strict`, but runtime access is).

There is **no shared fake base class** today. `tests/conftest.py::MockProvider` is the
documented shared stand-in, but most suites define local scripted providers. Each must be
updated (or inherit from one that is).

### Inventory (verified by scanning `tests/**/*.py` for `async def generate`)

**Already has `usage` (1):**

| Class | File |
|---|---|
| `DelayedProvider` | `tests/test_concurrency.py` — `self.usage = Usage()` in `__init__` |

**Missing `usage` — must add (22 classes):**

| File | Class | Notes |
|---|---|---|
| `tests/conftest.py` | `MockProvider` | shared fixture; add in `__init__` as `self.usage = Usage()` |
| `tests/test_condenser.py` | `ScriptedProvider`, `BoomProvider`, `FlakyProvider` | |
| `tests/test_pipeline.py` | `ScriptedProvider`, `PhaseAwareProvider`, `RoutingProvider`, `TranslateOnlyProvider`, `AllPhaseProvider` | top-level |
| `tests/test_pipeline.py` | `FailingProvider` | nested inside `test_translate_only_untranslated_warning` |
| `tests/test_synthesizer.py` | `QueueProvider`, `BoomProvider` | |
| `tests/test_toc_inference.py` | `ScriptedProvider` | |
| `tests/test_translator.py` | `ScriptedProvider`, `BatchCountingProvider`, `CountingProvider`, `PartialProvider`, `TagEchoProvider`, `TagManglingProvider`, `PoisonBatchProvider` | |
| `tests/test_usage.py` | `PlainProvider` | nested; also owns the obsolete assertion (below) |
| `tests/test_vision_ranker.py` | `FakeVisionProvider` | parent of subclasses |

**Inherit coverage — no edit needed if parent is fixed (3):**

| Class | Inherits | File |
|---|---|---|
| `VisionRoutingProvider` | `RoutingProvider` | `test_pipeline.py` |
| `BrokenProvider` (×2 nested) | `FakeVisionProvider` | `test_vision_ranker.py` |
| `_RaisingProvider` | `FakeVisionProvider` | `test_vision_ranker.py` |

**Not a provider (do not touch):** `BoomPdf` in `test_pipeline.py` — it is a fake *renderer*
(`render(...)`), not an LLM provider.

### Recommended pattern for fakes

Prefer instance attribute when the class already has `__init__` (matches production
providers and avoids shared mutable class state):

```python
def __init__(self, ...) -> None:
    ...
    self.usage = Usage()
```

For minimal fakes without `__init__`, a class attribute is acceptable **only if the fake
never calls `usage.add`** (true for almost all scripted providers today):

```python
class ScriptedProvider:
    name = "scripted"
    usage = Usage()
    ...
```

Import: `from breviabook.llm.usage import Usage` in each test module that does not already
import it (`test_concurrency.py` already does).

### Obsolete assertion — intentional contract change

`tests/test_usage.py::test_pipeline_usage_none_when_provider_untracked` currently:

```python
class PlainProvider:
    name = "plain"
    async def generate(...) -> str: ...

assert result.usage is None  # provider doesn't track usage
```

That encoded the old feat--usage-cost-reporting escape hatch. Under this PRP every
`LLMProvider` tracks usage, so:

1. Give `PlainProvider` a `usage = Usage()` (or `self.usage = Usage()`).
2. Rewrite the test to assert the new contract, e.g.:
   - rename to `test_pipeline_surfaces_empty_usage_when_fake_does_not_accumulate`
   - `assert result.usage is not None`
   - `assert result.usage.calls == 0` (fake never calls `usage.add`)

This is the **only** assertion change expected. Do not weaken other usage tests
(`test_pipeline_surfaces_usage`, `test_provider_accumulates_usage`, concurrency usage
asserts).

### `isinstance(provider, LLMProvider)` in the suite

Only production-path check found: `tests/test_llm_factory.py:147` —
`assert isinstance(provider, LLMProvider)` against a **real** factory-built provider
(already has `usage`). No test asserts `isinstance` on a bare scripted fake today, but
fixing the fakes keeps that door open and matches conftest's claim that `MockProvider`
"satisfies the `LLMProvider` Protocol structurally."

## Context & references

```yaml
- breviabook/llm/base.py              # LLMProvider Protocol — add usage
- breviabook/llm/usage.py             # Usage dataclass (import target)
- breviabook/llm/providers/litellm_base.py  # already self.usage = Usage()
- breviabook/llm/providers/ollama.py  # already self.usage = Usage()
- breviabook/pipeline.py              # getattr L326 + isinstance L334
- breviabook/cli.py                   # getattr L276, L406
- breviabook/ui/progress.py           # RunReporter(usage_source: Usage | None) — unchanged API
- tests/conftest.py                   # MockProvider
- tests/test_usage.py                 # escape-hatch test to rewrite
- tests/test_concurrency.py           # DelayedProvider already correct
- PRPs/feat--usage-cost-reporting.md  # original feature; escape hatch superseded here
```

## Implementation blueprint

1. **Re-baseline (report exact command output):**
   ```bash
   uv run pytest --collect-only -q | tail -1
   uv run pytest -q
   ```
   Expected at planning time: `282 tests collected`, `281 passed, 1 skipped`.

2. **Commit this PRP first** (new repo rule — plan before code):
   ```text
   docs(prp): track usage-protocol PRP
   ```
   Or fold the PRP into the single implementation commit if preferred; never leave it
   untracked when execution starts.

3. **Protocol** — `breviabook/llm/base.py`:
   ```python
   from breviabook.llm.usage import Usage

   @runtime_checkable
   class LLMProvider(Protocol):
       name: str
       usage: Usage

       async def generate(...) -> str: ...
   ```
   Update the class docstring one line: providers expose a live `Usage` accumulator.

4. **Pipeline** — `breviabook/pipeline.py` result assembly (today L326–334):
   ```python
   return CondenseResult(
       ...
       usage=provider.usage,
       ...
   )
   ```
   Remove `getattr` and the `isinstance(usage, Usage)` ternary. Keep
   `CondenseResult.usage: Usage | None = None` field type as-is (assigned `Usage` at
   runtime; default remains for the dataclass).

5. **CLI** — `breviabook/cli.py` L276 and L406:
   ```python
   usage_source = llm.usage
   ```
   `RunReporter(..., usage_source=usage_source)` already accepts `Usage | None`; passing
   `Usage` is fine. Post-run table still gates on `if usage and usage.calls`.

6. **Test doubles** — add `usage` to all 22 classes in the inventory table above.
   - `MockProvider`: `self.usage = Usage()` inside existing `__init__`.
   - Inheritance children of `RoutingProvider` / `FakeVisionProvider`: no extra line.
   - Ensure each touched file imports `Usage`.

7. **Rewrite** `test_pipeline_usage_none_when_provider_untracked` per the gotcha section
   (empty accumulator, not `None`).

8. **Validate** all five gates. Re-run collect-only and pytest; numbers must match
   baseline (same collected count; same passed/skipped).

9. **Single implementation commit** with trailer:
   ```text
   Co-Authored-By: Grok <noreply@x.ai>
   ```

### New / changed files

| Path | Change |
|---|---|
| `breviabook/llm/base.py` | `usage: Usage` on `LLMProvider`; import `Usage` |
| `breviabook/pipeline.py` | direct `provider.usage`; drop getattr/isinstance |
| `breviabook/cli.py` | direct `llm.usage` at both command sites |
| `tests/conftest.py` | `MockProvider.usage` |
| `tests/test_condenser.py` | 3 fakes |
| `tests/test_pipeline.py` | 6 fakes (5 top + FailingProvider) |
| `tests/test_synthesizer.py` | 2 fakes |
| `tests/test_toc_inference.py` | 1 fake |
| `tests/test_translator.py` | 7 fakes |
| `tests/test_usage.py` | PlainProvider + assertion rewrite |
| `tests/test_vision_ranker.py` | `FakeVisionProvider` |
| `PRPs/feat--usage-protocol.md` | this file (committed at execute start) |

No changes to real provider classes (already compliant).

## Validation gates (must all pass)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict breviabook
uv run pytest -q
uv run pip-licenses --partial-match --fail-on "General Public License;GPL" --ignore-packages pyphen
```

### Feature-specific checks

- [ ] `rg -n 'getattr\([^)]*usage' breviabook/` returns **no matches**.
- [ ] `LLMProvider` source shows `usage: Usage` next to `name: str`.
- [ ] `uv run mypy --strict breviabook` clean (Protocol import of `Usage` does not cycle —
      `usage.py` does not import `base.py`).
- [ ] Before/after from **command output**:
  - `uv run pytest --collect-only -q | tail -1` → same collected count (282 at planning).
  - `uv run pytest -q` → same summary (`281 passed, 1 skipped` at planning).
- [ ] `test_provider_accumulates_usage` and `test_pipeline_surfaces_usage` still green.
- [ ] Rewritten empty-usage test asserts `result.usage is not None` and `calls == 0`.
- [ ] `tests/test_llm_factory.py` `isinstance(provider, LLMProvider)` still green.
- [ ] No real LLM / network in tests.

## Acceptance criteria

- [ ] `usage: Usage` is part of the `LLMProvider` Protocol contract.
- [ ] Zero defensive `getattr(..., "usage", None)` in production code.
- [ ] Pipeline assigns `CondenseResult.usage = provider.usage` with no isinstance guard.
- [ ] Every test double that implements `generate` exposes `usage` (inventory complete).
- [ ] Old "untracked provider → None" contract explicitly replaced; suite still full green.
- [ ] Five quality gates green; pytest collect/pass counts match pre-change command output.
- [ ] PRP committed at execute time; one implementation commit with Grok co-author trailer;
      `AGENTS.md` still untracked.

## Confidence score

8/10 — Production change is three lines plus a Protocol field; real providers already
comply. Risk is almost entirely the long tail of local test fakes (22 classes, no shared
base) and the one intentional assertion rewrite. The inventory above was generated from the
tree, not recalled — still re-run the scan after edits to catch any new fake introduced
between planning and execute.
