# PRP: Phase 0 — Scaffold

> Product Requirement Prompt for **BreviaBook**. Goal: enough context + executable
> validation for one-pass implementation. Source of truth: [docs/ROADMAP.md](../docs/ROADMAP.md) §10 (Phase 0).
> Operating rules: [CLAUDE.md](../CLAUDE.md).

## Goal

Stand up a fully-tooled, CI-green Python project skeleton for BreviaBook: build config,
folder structure, config layer, a `typer` CLI skeleton, quality tooling, CI, license
files, and a **minimal LLM layer** (`base.py` + `factory.py` + Ollama provider) that
proves a "hello world" round-trip against local Ollama. After this PRP, `ruff` and
`mypy --strict` pass clean and `breviabook --help` works.

## Why

- Every later phase (PRP 001+) builds on this scaffold; getting tooling + license
  guardrails right *up front* is what keeps the project clean-room and AGPL-free.
- The LLM layer is the foundation the condenser/translator/vision-ranker all depend on.

## Scope

**In scope:**
- `pyproject.toml` (project metadata, deps, ruff + mypy + pytest config).
- Full package tree from ROADMAP §6 with `__init__.py` placeholders (empty modules OK
  where later phases fill them — but they must import clean).
- `breviabook/config.py` — pydantic-settings + `.env` loading.
- `breviabook/cli.py` — `typer` app with a `condense` command **stub** (parses all flags from
  ROADMAP §8, prints the parsed config; no pipeline yet) and a real `version` command.
- `breviabook/llm/base.py` (Protocol), `breviabook/llm/factory.py`, `breviabook/llm/providers/ollama.py`.
- `.env.example`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`.
- `LICENSE` (Apache-2.0) + `NOTICE` (attribution per ROADMAP §14).
- A first test: `tests/test_llm_factory.py` using a **deterministic mock provider**.

**Out of scope (later phases):** IR models, parsers, chunker, condenser, renderers,
extra providers (OpenAI/Gemini/OpenRouter), glossary, vision ranking. Leave those
modules as documented stubs.

## Non-negotiable constraints (from CLAUDE.md / ROADMAP §14)

- [ ] Clean-room: no code copied/translated from reference repos (TBL etc.).
- [ ] No GPL/AGPL runtime deps. **`ebooklib` and `PyMuPDF` are forbidden.** Permissive only.
- [ ] `.env`, `*.sqlite`, job DBs never committed (already in `.gitignore`).
- [ ] **SSRF guard:** never forward provider API keys to an arbitrary `--api-endpoint` host
      (only send Ollama/base_url calls to the configured endpoint). Stub the guard now in
      `breviabook/utils/security.py` even if endpoints land fully in Phase 9.

## Context & references

```yaml
- docs/ROADMAP.md          # §4 stack, §6 folder layout, §7.4 multi-provider, §8 CLI, §9 config, §10 Phase 0, §14 license
- CLAUDE.md                # operating rules, quality gates, stack
- PRPs/templates/prp_base.md

# External docs (verify versions when pinning):
- typer:    https://typer.tiangolo.com/
- pydantic-settings: https://docs.pydantic.dev/latest/concepts/pydantic_settings/
- litellm:  https://docs.litellm.ai/docs/providers/ollama
- ruff:     https://docs.astral.sh/ruff/
- uv:       https://docs.astral.sh/uv/
- ollama API: https://github.com/ollama/ollama/blob/main/docs/api.md
```

Patterns to **study, never copy** (ROADMAP §2): TBL `src/core/llm/` (provider protocol,
factory, key_pool) and `src/config.py`. Reimplement from this spec.

## Tech / decisions

- **Package manager: `uv`.** Python **3.11+**.
- **LLM layer = our own thin Protocol over `litellm`.** Define `base.py` as a `Protocol`
  with `async generate(messages, model, **opts) -> str`; implement `OllamaProvider` calling
  `litellm.acompletion(model=f"ollama/{model}", api_base=endpoint, ...)`. Using LiteLLM
  (MIT) instead of porting TBL's providers removes the AGPL temptation (ROADMAP §14) and
  unlocks Phase 9 providers cheaply. Keep our Protocol so the rest of the code never imports
  litellm directly.
- **Defaults (match installed Ollama models, see memory):** `DEFAULT_MODEL=gemma4:e4b`.
  Document `qwen3:30b` in `.env.example` comments as the higher-quality option for long-doc
  summarization.

## Implementation blueprint

1. `pyproject.toml`: `[project]` (name=breviabook, py>=3.11, console script `breviabook = "breviabook.cli:app"`),
   runtime deps (`typer`, `rich`, `pydantic>=2`, `pydantic-settings`, `python-dotenv`,
   `litellm`, `httpx`, `tiktoken`), dev deps (`ruff`, `mypy`, `pytest`, `pytest-asyncio`,
   `pip-licenses`, `pre-commit`). `[tool.ruff.lint] select = ["E","F","I","UP","B","SIM"]`.
   `[tool.mypy] strict = true`. `[tool.pytest.ini_options] asyncio_mode = "auto"`.
2. Create the package tree from ROADMAP §6 (`ir/`, `parsers/`, `llm/providers/`, `condense/`,
   `translate/`, `images/`, `render/`, `persistence/`, `utils/`) with `__init__.py`. Later-phase
   modules may be empty but must import clean and pass mypy.
3. `breviabook/config.py`: `Settings(BaseSettings)` mirroring ROADMAP §9 (`LLM_PROVIDER`,
   `OLLAMA_ENDPOINT`, `DEFAULT_MODEL`, API keys, `DEFAULT_TARGET_RATIO`, `DEFAULT_CHUNK_TOKENS`,
   `IMAGE_STRATEGY`). Keys accept comma-separated lists (rotation, future use).
4. `breviabook/llm/base.py`: `class LLMProvider(Protocol)` + a shared message type.
   `breviabook/llm/factory.py`: `get_provider(name, settings) -> LLMProvider`.
   `breviabook/llm/providers/ollama.py`: `OllamaProvider` via litellm.
5. `breviabook/utils/security.py`: `validate_safe_path()` (zip-slip, used Phase 1) + helper that
   refuses to attach API keys when the target host isn't the configured provider host (SSRF).
6. `breviabook/cli.py`: `typer.Typer()`; `version` command; `condense` command accepting every flag
   in ROADMAP §8 → builds config, prints it with `rich` (no LLM call yet — full pipeline lands
   later). Make `--dry-run` short-circuit cleanly.
7. `.env.example` (from §9, with the model-comment note), `.pre-commit-config.yaml`
   (ruff lint+format, mypy, pytest), `.github/workflows/ci.yml`
   (uv → ruff check + ruff format --check + mypy --strict + pytest + `pip-licenses --fail-on "GPL"`).
8. `LICENSE` = Apache-2.0 full text (copyright "William Wong Garay"); `NOTICE` with the
   ROADMAP §14 attribution ("architecture studied and inspired by TranslateBooksWithLLMs and
   ollama-ebook-summary; no code reused").
9. `tests/test_llm_factory.py`: a `MockProvider` (deterministic) + assert factory wiring and
   that `generate` is awaitable/returns the canned string. No real network/LLM in tests.

### New / changed files

- `pyproject.toml`, `.env.example`, `.pre-commit-config.yaml`, `.github/workflows/ci.yml`
- `LICENSE`, `NOTICE`
- `breviabook/__init__.py`, `breviabook/cli.py`, `breviabook/config.py`
- `breviabook/llm/{base,factory}.py`, `breviabook/llm/providers/ollama.py`
- `breviabook/utils/security.py`
- package `__init__.py` stubs across the §6 tree
- `tests/__init__.py`, `tests/conftest.py`, `tests/test_llm_factory.py`

## Validation gates (must all pass)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict breviabook
uv run pytest -q
uv run pip-licenses --fail-on "GPL"
```

### Feature-specific checks

- [ ] `uv run breviabook --help` and `uv run breviabook version` work.
- [ ] `uv run breviabook condense book.epub --dry-run --provider ollama --model gemma4:e4b`
      parses and prints the resolved config without calling an LLM.
- [ ] Factory returns an `OllamaProvider` for `--provider ollama`; unknown provider raises a
      clear error.
- [ ] (Manual, optional) a tiny script calling `OllamaProvider.generate` returns text from local
      `gemma4:e4b` — proves the hello-world round-trip. Not in the automated suite.
- [ ] `pip-licenses` output contains no GPL/AGPL entries.

## Acceptance criteria

- [ ] Project installs (`uv sync`) and `breviabook` console script runs.
- [ ] All five validation gates green from the first commit.
- [ ] LICENSE (Apache-2.0) + NOTICE present and correct.
- [ ] LLM layer is litellm-backed behind our own Protocol; nothing outside `llm/` imports litellm.

## Confidence score

8/10 — Main risk is litellm's exact Ollama call signature and pinning a transitive dep that
trips `pip-licenses`; both are quick to resolve during implementation.
