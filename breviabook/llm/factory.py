"""Provider factory — selects an ``LLMProvider`` by name (ROADMAP §7.4).

Ollama is local (no key). OpenAI/Gemini/OpenRouter use litellm with a key-rotation pool built
from the configured comma-separated keys. OpenAI also accepts ``api_endpoint`` (base_url) for
OpenAI-compatible servers; only OpenAI does, and the endpoint is validated (explicit http(s)
scheme; canonical or provably local host) before any configured key is attached, so a provider
never sends its key to another host (ROADMAP §12).
"""

from __future__ import annotations

from urllib.parse import urlparse

from breviabook.config import Settings
from breviabook.llm.base import LLMProvider
from breviabook.llm.key_pool import KeyPool
from breviabook.llm.providers.gemini import GeminiProvider
from breviabook.llm.providers.litellm_base import LiteLLMProvider
from breviabook.llm.providers.ollama import OllamaProvider
from breviabook.llm.providers.openai import OpenAIProvider
from breviabook.llm.providers.openrouter import OpenRouterProvider
from breviabook.utils.security import assert_endpoint_allowed, is_local_host

_SUPPORTED = ("ollama", "openai", "gemini", "openrouter")
_OPENAI_HOST = "api.openai.com"
_ENV_VAR = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

# Providers whose models "think" by default and bill that reasoning as output tokens. For
# BreviaBook's rewriting tasks (condense/translate) thinking adds cost, not quality, so we turn it
# off by default. Users can pass --reasoning-effort auto to restore the provider's native mode.
_THINKING_ON_BY_DEFAULT = ("gemini",)


def _resolve_reasoning_effort(provider_key: str, requested: str | None) -> str | None:
    if requested == "auto":
        return None  # honor the provider's native default (e.g. Gemini dynamic thinking)
    if requested:
        return requested
    if provider_key in _THINKING_ON_BY_DEFAULT:
        return "disable"
    return None


def _assert_endpoint_safe(endpoint: str, *, keys_configured: bool) -> None:
    """Reject unsafe OpenAI-compatible endpoints before any credentials are attached.

    Always requires an explicit http(s) scheme. With real keys configured, the host must
    be OpenAI's canonical host or a provably local one — a configured cloud key is never
    forwarded to an arbitrary host (SSRF / key-leakage, ROADMAP §12).
    """
    if urlparse(endpoint).scheme not in ("http", "https"):
        raise ValueError(f"Invalid --api-endpoint {endpoint!r}: must include http:// or https://")
    if not keys_configured:
        return
    host = urlparse(endpoint).hostname or ""
    allowed = {_OPENAI_HOST}
    if is_local_host(host):
        allowed.add(host)
    assert_endpoint_allowed(endpoint, allowed)


def _missing_key(provider: str) -> ValueError:
    return ValueError(
        f"No API key for {provider!r}. Set {_ENV_VAR[provider]} in your environment or .env "
        "(comma-separated for key rotation)."
    )


def get_provider(
    name: str,
    settings: Settings,
    *,
    api_endpoint: str | None = None,
    reasoning_effort: str | None = None,
) -> LLMProvider:
    """Return a provider instance for ``name``.

    ``reasoning_effort`` (``"disable"``/``"low"``/``"medium"``/``"high"``, or ``"auto"`` to keep
    the provider's native default) controls a reasoning model's thinking budget. When left as
    ``None`` it defaults to ``"disable"`` for providers that think by default (Gemini), since
    thinking is wasted cost for condensation/translation. Applied only to litellm-backed
    providers and ignored by providers that do not support it.

    Raises:
        ValueError: for an unknown provider or a missing required API key.
    """
    key = name.lower()
    provider = _build(key, settings, api_endpoint)
    effort = _resolve_reasoning_effort(key, reasoning_effort)
    if effort and isinstance(provider, LiteLLMProvider):
        provider.extra_opts["reasoning_effort"] = effort
    return provider


def _build(key: str, settings: Settings, api_endpoint: str | None) -> LLMProvider:
    if key == "ollama":
        return OllamaProvider(endpoint=api_endpoint or settings.ollama_endpoint)
    if key == "openai":
        keys = settings.keys_for("openai")
        if api_endpoint:
            _assert_endpoint_safe(api_endpoint, keys_configured=bool(keys))
        if not keys:
            if not api_endpoint:
                raise _missing_key("openai")
            keys = ["EMPTY"]  # local OpenAI-compatible servers ignore the key
        return OpenAIProvider(KeyPool(keys), base_url=api_endpoint)
    if key == "gemini":
        keys = settings.keys_for("gemini")
        if not keys:
            raise _missing_key("gemini")
        return GeminiProvider(KeyPool(keys))
    if key == "openrouter":
        keys = settings.keys_for("openrouter")
        if not keys:
            raise _missing_key("openrouter")
        return OpenRouterProvider(KeyPool(keys))
    raise ValueError(
        f"Unknown or unsupported provider {key!r}. Supported: {', '.join(_SUPPORTED)}."
    )
