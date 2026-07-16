"""Phase 0 smoke tests: factory wiring + Protocol conformance via the mock provider."""

from __future__ import annotations

import pytest

from breviabook.config import Settings
from breviabook.llm.base import LLMProvider
from breviabook.llm.factory import get_provider
from breviabook.llm.providers.gemini import GeminiProvider
from breviabook.llm.providers.ollama import OllamaProvider
from breviabook.llm.providers.openai import OpenAIProvider
from breviabook.llm.providers.openrouter import OpenRouterProvider
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
    # Explicit empty keys so the test is independent of any ambient .env.
    empty = Settings(openai_api_key="", gemini_api_key="", openrouter_api_key="")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        get_provider("openai", empty)
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        get_provider("gemini", empty)


def test_openai_compatible_endpoint_without_key() -> None:
    # A local OpenAI-compatible server needs no real key.
    empty = Settings(openai_api_key="")
    provider = get_provider("openai", empty, api_endpoint="http://localhost:1234/v1")
    assert isinstance(provider, OpenAIProvider)
    assert provider.base_url == "http://localhost:1234/v1"


def test_ollama_endpoint_override() -> None:
    provider = get_provider("ollama", Settings(), api_endpoint="http://host:9999")
    assert isinstance(provider, OllamaProvider)
    assert provider.endpoint == "http://host:9999"


def test_gemini_defaults_to_thinking_disabled() -> None:
    settings = Settings(gemini_api_key="g")
    provider = get_provider("gemini", settings)
    assert provider.extra_opts["reasoning_effort"] == "disable"  # type: ignore[attr-defined]


def test_reasoning_effort_auto_keeps_native_thinking() -> None:
    settings = Settings(gemini_api_key="g")
    provider = get_provider("gemini", settings, reasoning_effort="auto")
    assert "reasoning_effort" not in provider.extra_opts  # type: ignore[attr-defined]


def test_explicit_reasoning_effort_overrides_default() -> None:
    settings = Settings(gemini_api_key="g")
    provider = get_provider("gemini", settings, reasoning_effort="high")
    assert provider.extra_opts["reasoning_effort"] == "high"  # type: ignore[attr-defined]


def test_openai_has_no_default_reasoning_effort() -> None:
    settings = Settings(openai_api_key="k")
    provider = get_provider("openai", settings)
    assert "reasoning_effort" not in provider.extra_opts  # type: ignore[attr-defined]


_FAKE_KEY = "sk-fake-key-must-never-leak"


def test_real_key_refused_on_public_custom_endpoint() -> None:
    settings = Settings(openai_api_key=_FAKE_KEY)
    with pytest.raises(ValueError, match="attacker.example") as exc_info:
        get_provider("openai", settings, api_endpoint="https://attacker.example/v1")
    assert _FAKE_KEY not in str(exc_info.value)


def test_real_key_refused_on_bare_lan_hostname() -> None:
    # Bare single-label hostnames resolve via search-domain and are not provably private.
    settings = Settings(openai_api_key=_FAKE_KEY)
    with pytest.raises(ValueError, match="gpubox") as exc_info:
        get_provider("openai", settings, api_endpoint="http://gpubox:8000/v1")
    msg = str(exc_info.value)
    assert "private IP" in msg
    assert _FAKE_KEY not in msg


def test_endpoint_without_scheme_fails_fast() -> None:
    with pytest.raises(ValueError, match="must include http:// or https://"):
        get_provider("openai", Settings(openai_api_key=""), api_endpoint="localhost:1234")


def test_real_key_allowed_on_canonical_openai_host() -> None:
    settings = Settings(openai_api_key="k")
    provider = get_provider("openai", settings, api_endpoint="https://api.openai.com/v1")
    assert isinstance(provider, OpenAIProvider)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:1234/v1",
        "http://127.0.0.1:1234/v1",
        "http://192.168.1.50:8000/v1",
        "http://[::1]:1234/v1",
        "http://nas.local:8000/v1",
    ],
)
def test_real_key_allowed_on_local_endpoints(endpoint: str) -> None:
    settings = Settings(openai_api_key="k")
    provider = get_provider("openai", settings, api_endpoint=endpoint)
    assert isinstance(provider, OpenAIProvider)
    assert provider.base_url == endpoint


def test_no_key_custom_endpoint_on_public_host_still_works() -> None:
    # Without a configured key there is nothing to leak; the EMPTY placeholder is sent.
    empty = Settings(openai_api_key="")
    provider = get_provider("openai", empty, api_endpoint="https://vllm.example.com/v1")
    assert isinstance(provider, OpenAIProvider)
    assert provider.base_url == "https://vllm.example.com/v1"


def test_mock_provider_satisfies_protocol() -> None:
    provider = MockProvider()
    assert isinstance(provider, LLMProvider)


async def test_mock_provider_generate_is_deterministic() -> None:
    provider = MockProvider(reply="hello world")
    out = await provider.generate([{"role": "user", "content": "hi"}], model="x")
    assert out == "hello world"
    assert provider.calls == [([{"role": "user", "content": "hi"}], "x")]
