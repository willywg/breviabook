"""Phase 0 smoke tests: factory wiring + Protocol conformance via the mock provider."""

from __future__ import annotations

import pytest

from brevia.config import Settings
from brevia.llm.base import LLMProvider
from brevia.llm.factory import get_provider
from brevia.llm.providers.ollama import OllamaProvider
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


def test_mock_provider_satisfies_protocol() -> None:
    provider = MockProvider()
    assert isinstance(provider, LLMProvider)


async def test_mock_provider_generate_is_deterministic() -> None:
    provider = MockProvider(reply="hello world")
    out = await provider.generate([{"role": "user", "content": "hi"}], model="x")
    assert out == "hello world"
    assert provider.calls == [([{"role": "user", "content": "hi"}], "x")]
