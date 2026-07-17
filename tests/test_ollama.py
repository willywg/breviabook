"""Ollama retry/backoff and provider wiring (no network, no litellm)."""

from __future__ import annotations

from typing import Any

import pytest

from breviabook.llm.providers.ollama import OllamaProvider
from breviabook.llm.rate_limit import retry_with_backoff


class RateLimitError(Exception):
    pass


class APIConnectionError(Exception):
    pass


class OtherError(Exception):
    pass


async def _noop_sleep(_seconds: float) -> None:
    return None


def _response(text: str) -> dict[str, Any]:
    return {"choices": [{"message": {"content": text}}]}


async def test_retry_success_first_try() -> None:
    calls = {"n": 0}

    async def call() -> str:
        calls["n"] += 1
        return "ok"

    assert await retry_with_backoff(call, sleep=_noop_sleep) == "ok"
    assert calls["n"] == 1


async def test_retry_backoff_on_connection_error() -> None:
    slept: list[float] = []
    calls = {"n": 0}

    async def call() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise APIConnectionError
        return "ok"

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    assert await retry_with_backoff(call, sleep=sleep) == "ok"
    assert calls["n"] == 2
    assert slept == [0.5]


async def test_retry_exponential_backoff() -> None:
    slept: list[float] = []
    calls = {"n": 0}

    async def call() -> str:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise APIConnectionError
        return "ok"

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    assert await retry_with_backoff(call, sleep=sleep) == "ok"
    assert calls["n"] == 3
    assert slept == [0.5, 1.0]


async def test_retry_bounded_by_max_retries() -> None:
    calls = {"n": 0}

    async def call() -> str:
        calls["n"] += 1
        raise APIConnectionError

    with pytest.raises(APIConnectionError):
        await retry_with_backoff(call, max_retries=2, sleep=_noop_sleep)
    assert calls["n"] == 3


async def test_retry_non_retryable_propagates() -> None:
    calls = {"n": 0}

    async def call() -> str:
        calls["n"] += 1
        raise OtherError("boom")

    with pytest.raises(OtherError, match="boom"):
        await retry_with_backoff(call, sleep=_noop_sleep)
    assert calls["n"] == 1


async def test_ollama_success_first_try() -> None:
    seen: dict[str, Any] = {}

    async def completer(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return _response("hi")

    provider = OllamaProvider(endpoint="http://ollama:11434", completer=completer)
    out = await provider.generate([{"role": "user", "content": "x"}], "qwen3:14b")
    assert out == "hi"
    assert seen["model"] == "ollama/qwen3:14b"
    assert seen["api_base"] == "http://ollama:11434"


async def test_ollama_generate_retries_then_succeeds() -> None:
    slept: list[float] = []
    calls = {"n": 0}

    async def completer(**kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise APIConnectionError
        return _response("recovered")

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    provider = OllamaProvider(completer=completer, sleep=sleep)
    out = await provider.generate([{"role": "user", "content": "x"}], "qwen3:14b")
    assert out == "recovered"
    assert calls["n"] == 2
    assert slept == [0.5]


async def test_ollama_generate_with_image_retries() -> None:
    seen: dict[str, Any] = {}
    calls = {"n": 0}

    async def completer(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        calls["n"] += 1
        if calls["n"] == 1:
            raise RateLimitError
        return _response("seen")

    provider = OllamaProvider(
        endpoint="http://localhost:11434",
        completer=completer,
        sleep=_noop_sleep,
    )
    out = await provider.generate_with_image("describe", [(b"\x89PNG", "image/png")], "qwen3-vl")
    assert out == "seen"
    assert calls["n"] == 2
    assert seen["api_base"] == "http://localhost:11434"
    content = seen["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "describe"}
    assert content[1]["type"] == "image_url"


async def test_ollama_non_retryable_fails_fast() -> None:
    calls = {"n": 0}

    async def completer(**kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        raise OtherError("fail")

    provider = OllamaProvider(completer=completer, sleep=_noop_sleep)
    with pytest.raises(OtherError, match="fail"):
        await provider.generate([{"role": "user", "content": "x"}], "qwen3:14b")
    assert calls["n"] == 1
