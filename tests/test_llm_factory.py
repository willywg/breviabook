"""Phase 0 smoke tests: factory wiring + Protocol conformance via the mock provider."""

from __future__ import annotations

import pytest

from brevia.config import Settings
from brevia.llm.base import LLMProvider
from brevia.llm.factory import get_provider
from brevia.llm.providers.gemini import GeminiProvider
from brevia.llm.providers.ollama import OllamaProvider
from brevia.llm.providers.openai import OpenAIProvider
from brevia.llm.providers.openrouter import OpenRouterProvider
from tests.conftest import MockProvider


def test_factory_returns_ollama_provider() -> None:
    settings = Settings(ollama_endpoint="http://localhost:11434")
    provider = get_provider("ollama", settings)
    assert isinstance(provider, OllamaProvider)
    assert provider.endpoint == "http://localhost:11434"


def test_factory_is_case_insensitive() -> None:
    assert isinstance(get_provider("OLLAMA", Settings()), OllamaProvider)


def test_factory_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="Unknown or unsupported provider"):
        get_provider("does-not-exist", Settings())


def test_factory_builds_cloud_providers_with_keys() -> None:
    settings = Settings(openai_api_key="k1,k2", gemini_api_key="g", openrouter_api_key="o")
    assert isinstance(get_provider("openai", settings), OpenAIProvider)
    assert isinstance(get_provider("gemini", settings), GeminiProvider)
    assert isinstance(get_provider("openrouter", settings), OpenRouterProvider)
    # comma-separated keys populate the rotation pool
    assert len(get_provider("openai", settings).pool) == 2  # type: ignore[attr-defined]


def test_factory_missing_key_raises_with_env_var() -> None:
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        get_provider("openai", Settings())
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        get_provider("gemini", Settings())


def test_openai_compatible_endpoint_without_key() -> None:
    # A local OpenAI-compatible server needs no real key.
    provider = get_provider("openai", Settings(), api_endpoint="http://localhost:1234/v1")
    assert isinstance(provider, OpenAIProvider)
    assert provider.base_url == "http://localhost:1234/v1"


def test_ollama_endpoint_override() -> None:
    provider = get_provider("ollama", Settings(), api_endpoint="http://host:9999")
    assert isinstance(provider, OllamaProvider)
    assert provider.endpoint == "http://host:9999"


def test_mock_provider_satisfies_protocol() -> None:
    provider = MockProvider()
    assert isinstance(provider, LLMProvider)


async def test_mock_provider_generate_is_deterministic() -> None:
    provider = MockProvider(reply="hello world")
    out = await provider.generate([{"role": "user", "content": "hi"}], model="x")
    assert out == "hello world"
    assert provider.calls == [([{"role": "user", "content": "hi"}], "x")]
