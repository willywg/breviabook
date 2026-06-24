"""Provider factory — selects an ``LLMProvider`` by name (ROADMAP §7.4).

Phase 0 ships Ollama only. OpenAI (+ OpenAI-compatible via ``base_url``), Gemini, and
OpenRouter land in Phase 9 and register here.
"""

from __future__ import annotations

from brevia.config import Settings
from brevia.llm.base import LLMProvider
from brevia.llm.providers.ollama import OllamaProvider

_SUPPORTED = ("ollama",)


def get_provider(name: str, settings: Settings) -> LLMProvider:
    """Return a provider instance for ``name``.

    Raises:
        ValueError: for an unknown / not-yet-implemented provider.
    """
    key = name.lower()
    if key == "ollama":
        return OllamaProvider(endpoint=settings.ollama_endpoint)
    raise ValueError(
        f"Unknown or unsupported provider {name!r}. Supported: {', '.join(_SUPPORTED)}. "
        "(OpenAI/Gemini/OpenRouter arrive in Phase 9.)"
    )
