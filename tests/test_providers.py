"""litellm-backed providers via an injected completer (no network, no litellm)."""

from __future__ import annotations

from typing import Any

from brevia.llm.key_pool import KeyPool
from brevia.llm.providers.gemini import GeminiProvider
from brevia.llm.providers.openai import OpenAIProvider
from brevia.llm.providers.openrouter import OpenRouterProvider


def _response(text: str) -> dict[str, Any]:
    return {"choices": [{"message": {"content": text}}]}


async def test_openai_provider_sends_route_key_and_returns_content() -> None:
    seen: dict[str, Any] = {}

    async def completer(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return _response("hi")

    provider = OpenAIProvider(KeyPool(["k1"]), completer=completer)
    out = await provider.generate([{"role": "user", "content": "x"}], "gpt-4o")
    assert out == "hi"
    assert seen["model"] == "openai/gpt-4o"
    assert seen["api_key"] == "k1"
    assert seen["api_base"] is None


async def test_openai_compatible_base_url() -> None:
    seen: dict[str, Any] = {}

    async def completer(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return _response("ok")

    provider = OpenAIProvider(
        KeyPool(["EMPTY"]), base_url="http://localhost:1234/v1", completer=completer
    )
    await provider.generate([{"role": "user", "content": "x"}], "local-model")
    assert seen["api_base"] == "http://localhost:1234/v1"


async def test_gemini_and_openrouter_routes() -> None:
    routes: list[str] = []

    async def completer(**kwargs: Any) -> dict[str, Any]:
        routes.append(kwargs["model"])
        return _response("ok")

    await GeminiProvider(KeyPool(["g"]), completer=completer).generate(
        [{"role": "user", "content": "x"}], "gemini-2.0"
    )
    await OpenRouterProvider(KeyPool(["o"]), completer=completer).generate(
        [{"role": "user", "content": "x"}], "meta/llama"
    )
    assert routes == ["gemini/gemini-2.0", "openrouter/meta/llama"]


async def test_provider_rotates_key_on_rate_limit() -> None:
    class RateLimitError(Exception):
        pass

    keys_used: list[str | None] = []
    calls = {"n": 0}

    async def completer(**kwargs: Any) -> dict[str, Any]:
        keys_used.append(kwargs["api_key"])
        calls["n"] += 1
        if calls["n"] == 1:
            raise RateLimitError
        return _response("recovered")

    provider = OpenAIProvider(KeyPool(["k1", "k2"]), completer=completer, max_retries=2)
    out = await provider.generate([{"role": "user", "content": "x"}], "gpt-4o")
    assert out == "recovered"
    assert keys_used == ["k1", "k2"]  # rotated to the second key after the rate-limit error
