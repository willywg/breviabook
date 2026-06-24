"""Provider factory — selects an ``LLMProvider`` by name (ROADMAP §7.4).

Ollama is local (no key). OpenAI/Gemini/OpenRouter use litellm with a key-rotation pool built
from the configured comma-separated keys. OpenAI also accepts ``api_endpoint`` (base_url) for
OpenAI-compatible servers; only OpenAI does, so a provider never sends its key to another
provider's host (ROADMAP §12).
"""

from __future__ import annotations

from brevia.config import Settings
from brevia.llm.base import LLMProvider
from brevia.llm.key_pool import KeyPool
from brevia.llm.providers.gemini import GeminiProvider
from brevia.llm.providers.ollama import OllamaProvider
from brevia.llm.providers.openai import OpenAIProvider
from brevia.llm.providers.openrouter import OpenRouterProvider

_SUPPORTED = ("ollama", "openai", "gemini", "openrouter")
_ENV_VAR = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def _missing_key(provider: str) -> ValueError:
    return ValueError(
        f"No API key for {provider!r}. Set {_ENV_VAR[provider]} in your environment or .env "
        "(comma-separated for key rotation)."
    )


def get_provider(name: str, settings: Settings, *, api_endpoint: str | None = None) -> LLMProvider:
    """Return a provider instance for ``name``.

    Raises:
        ValueError: for an unknown provider or a missing required API key.
    """
    key = name.lower()
    if key == "ollama":
        return OllamaProvider(endpoint=api_endpoint or settings.ollama_endpoint)
    if key == "openai":
        keys = settings.keys_for("openai")
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
        f"Unknown or unsupported provider {name!r}. Supported: {', '.join(_SUPPORTED)}."
    )
