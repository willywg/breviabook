# PRP: Wire the SSRF / key-leak guard on `--api-endpoint`

> Product Requirement Prompt for **BreviaBook**. Security fix (relates to ROADMAP §12 and
> closes the debt left by PRP 009).
> Operating rules: [CLAUDE.md](../CLAUDE.md). Builds on PRP 000 (security stubs) and PRP 009 (providers).

## Goal

`assert_endpoint_allowed()` exists in `breviabook/utils/security.py` since Phase 0 but is
**never called**. Today, `--provider openai --api-endpoint https://attacker.example` with a
real `OPENAI_API_KEY` configured sends that key to the attacker host. Wire the guard into
`llm/factory.py` so a configured cloud key is only attached when the target host is the
provider's canonical host or a local/private one.

## Why

- Real key-leak vector: any doc/script suggesting an `--api-endpoint` can exfiltrate the
  user's configured OpenAI key. README §security ("Keys are sent only to the selected
  provider's endpoint") currently describes a promise the code doesn't keep.
- ROADMAP §12 (line ~321) and PRP 009's constraint "No cross-provider key leakage" both
  mandate this; PRP 009 shipped the endpoint without the guard.

## Scope

**In scope:**
- `utils/security.py`:
  - Add `is_local_host(host)` helper — conservative: IP literals via `ipaddress`
    (loopback/private/link-local/reserved) plus `localhost` / `*.localhost` / `.local`
    hostnames. **Bare single-label hostnames (e.g. `gpubox`) are NOT local**: they resolve
    via search-domain and there's no guarantee they're private.
  - Extend `assert_endpoint_allowed`'s `ValueError` with remediation (see below).
  - Remove `safe_extract_path` (dead code: the EPUB parser reads in-memory via `zf.read()`,
    never extracts to disk). Leave a one-line note in the module docstring: there is no
    disk extraction, so the live zip-slip guard is `resolve_archive_href`.
  - Update the stale docstring ("Fully wired in Phase 9" → wired in the factory).
- `llm/factory.py`: in the `openai` branch of `_build`:
  1. If `api_endpoint` lacks an `http://`/`https://` scheme (e.g. `localhost:1234` →
     `urlparse().hostname` is `None`), raise a clear `ValueError`
     ("must include http:// or https://") **before** any host check — always, with or
     without keys.
  2. If real keys are configured, build the allowed set (`{"api.openai.com"}` + the
     endpoint host iff `is_local_host`) and call
     `assert_endpoint_allowed(api_endpoint, allowed)`.
- The no-key path (`keys = ["EMPTY"]` for local OpenAI-compatible servers) stays as-is:
  with no real key there is nothing to leak (scheme validation still applies).
- Tests: new `tests/test_security.py`; extend `tests/test_llm_factory.py`; adjust any test
  referencing `safe_extract_path`.

**Out of scope:**
- An explicit user override for authenticated remote vLLM on a public host (e.g. an env-var
  allowlist). If that use case appears, add it deliberately later — don't ship a footgun now.
- Ollama endpoint: no credentials are attached, nothing to guard.
- Gemini/OpenRouter: they don't accept a custom endpoint at all (already safe by design).

## Non-negotiable constraints (CLAUDE.md)

- [ ] No new runtime deps (`ipaddress` is stdlib).
- [ ] The refusal must be a clear `ValueError` naming the refused host **with remediation**:
      use the server's private IP directly (or a `.local` name), unset `OPENAI_API_KEY`,
      or use a local endpoint — never a silent fallback.
- [ ] Legitimate flows keep working: default OpenAI cloud, keyless local endpoint, and
      endpoints reached by private IP or `.local` name with keys configured.
- [ ] Error messages and tests must never print the key itself (mask — security taste rule).

## Context & references

```yaml
# Files to read/follow in this repo:
- breviabook/utils/security.py     # assert_endpoint_allowed (the unwired guard), zip-slip helpers
- breviabook/llm/factory.py        # _build() openai branch — the leak site (lines ~80-85)
- breviabook/config.py             # Settings.keys_for() — comma-separated key splitting
- tests/test_llm_factory.py        # factory test patterns (Settings injected, no network)
- docs/ROADMAP.md §12              # SSRF threat-model note (TBL — #211)
- PRPs/000--phase-0-scaffold.md    # original guard contract ("refuses to attach API keys…")
- PRPs/009--phase-9-more-providers.md  # "No cross-provider key leakage" constraint
```

## Implementation blueprint

1. `security.py`:
   - `is_local_host(host: str) -> bool` — `ipaddress.ip_address(host)` →
     `is_loopback | is_private | is_link_local | is_reserved`; on `ValueError` (hostname)
     → `True` iff `localhost`, ends with `.localhost`, or ends with `.local`. Bare
     single-label hostnames return `False`.
   - `assert_endpoint_allowed` error message gains remediation:
     "... If this is your own server, use its private IP or a .local name, or unset the
     provider API key to connect without credentials."
   - Delete `safe_extract_path`; rewrite the module docstring (guard now wired; one-line
     note that EPUB is read in-memory so no filesystem zip-slip guard is needed).
2. `factory.py` `_build`, openai branch:
   ```python
   if api_endpoint:
       if urlparse(api_endpoint).scheme not in ("http", "https"):
           raise ValueError(
               f"Invalid --api-endpoint {api_endpoint!r}: must include http:// or https://"
           )
       if keys:  # real keys configured → never forward them to an untrusted host
           allowed = {"api.openai.com"}
           host = urlparse(api_endpoint).hostname or ""
           if is_local_host(host):
               allowed.add(host)
           assert_endpoint_allowed(api_endpoint, allowed)
   ```
   placed before the `if not keys:` fallback; import the helpers from
   `breviabook.utils.security` and `urlparse` from `urllib.parse`.
3. Tests.

### New / changed files

- `breviabook/utils/security.py` — `is_local_host`, message remediation, remove
  `safe_extract_path`, docstring rewrite.
- `breviabook/llm/factory.py` — scheme validation + wire the guard in the openai branch.
- `tests/test_security.py` — new: guard + helper unit tests.
- `tests/test_llm_factory.py` — leak-scenario tests at the factory level.

## Validation gates (must all pass)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict breviabook
uv run pytest -q
uv run pip-licenses --fail-on "GPL"
```

### Feature-specific checks

- [ ] Real key + `--api-endpoint https://attacker.example` → `ValueError` naming the host
      with remediation; the key string never appears in the message (assert it in tests).
- [ ] Real key + bare LAN hostname (`http://gpubox:8000`) → **refused** with the same
      remediation (bare hostnames are not provably private).
- [ ] Real key + endpoint without scheme (`localhost:1234`) → `ValueError`
      "must include http:// or https://".
- [ ] Real key + no endpoint → works (default OpenAI cloud).
- [ ] Real key + `https://api.openai.com/v1` endpoint → works.
- [ ] Real key + local endpoints (`http://localhost:1234/v1`, `http://127.0.0.1:1234`,
      `http://192.168.1.50:8000`, `http://[::1]:1234`) → works.
- [ ] No key + any endpoint → works with the `"EMPTY"` placeholder (current behavior).
- [ ] `is_local_host` unit-tested: loopback/private/link-local IPs pass; public IPs,
      `attacker.example`, and bare `gpubox` fail.
- [ ] `assert_endpoint_allowed` unit-tested (allowed host passes, refused host raises with
      remediation in the message).
- [ ] No remaining references to `safe_extract_path` anywhere in the repo.

## Acceptance criteria

- [ ] A configured cloud key cannot be forwarded to a public host that isn't the provider's
      canonical host, nor to an unqualified LAN hostname.
- [ ] All documented local workflows unaffected: Ollama, LM Studio/LocalAI/vLLM reached by
      localhost, private IP, or `.local` name.
- [ ] Scheme-less endpoints fail fast with an actionable message.
- [ ] `security.py` docstring no longer claims the guard is deferred; `safe_extract_path`
      is gone with a one-line rationale in its place.
- [ ] All validation gates green.

## Confidence score

9/10 — Small, well-bounded change with existing test patterns; policy decisions (bare
hostnames refused, no remote-vLLM override) were reviewed and are explicit.
