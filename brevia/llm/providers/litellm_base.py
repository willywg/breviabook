"""Base for litellm-backed providers (OpenAI, Gemini, OpenRouter) — ROADMAP §7.4.

Each provider differs only by its litellm route prefix (``openai/``, ``gemini/``,
``openrouter/``) and an optional ``base_url`` (for OpenAI-compatible endpoints). Calls go
through key rotation. litellm is lazy-imported (kept out of import time / test paths); tests
inject a ``completer`` instead.
"""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from typing import Any

from brevia.llm.base import Message
from brevia.llm.key_pool import KeyPool
from brevia.llm.rate_limit import with_key_rotation
from brevia.llm.usage import Usage, extract_usage

Completer = Callable[..., Awaitable[Any]]


def _extract_content(response: Any) -> str:
    content = response["choices"][0]["message"]["content"]
    return str(content) if content is not None else ""


def _data_uri(mime: str, data: bytes) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime or 'application/octet-stream'};base64,{encoded}"


def completion_cost(response: Any) -> float:
    """Best-effort USD cost for a litellm response; 0.0 if the model price is unknown."""
    try:
        import litellm

        return float(litellm.completion_cost(completion_response=response) or 0.0)
    except Exception:
        return 0.0


class LiteLLMProvider:
    """An :class:`~brevia.llm.base.LLMProvider` backed by litellm with key rotation."""

    def __init__(
        self,
        *,
        name: str,
        route: str,
        pool: KeyPool,
        base_url: str | None = None,
        max_retries: int = 3,
        completer: Completer | None = None,
    ) -> None:
        self.name = name
        self.route = route
        self.pool = pool
        self.base_url = base_url
        self.max_retries = max_retries
        self._completer = completer
        self.usage = Usage()

    async def _acompletion(self, **kwargs: Any) -> Any:
        if self._completer is not None:
            return await self._completer(**kwargs)
        import litellm

        return await litellm.acompletion(**kwargs)

    async def _run(self, messages: list[Any], model: str, **opts: object) -> str:
        async def call(key: str | None) -> str:
            response = await self._acompletion(
                model=f"{self.route}/{model}",
                messages=messages,
                api_key=key,
                api_base=self.base_url,
                **opts,
            )
            prompt, completion, cached = extract_usage(response)
            cost = completion_cost(response) if self._completer is None else 0.0
            self.usage.add(prompt, completion, cached, cost)
            return _extract_content(response)

        return await with_key_rotation(self.pool, call, max_retries=self.max_retries)

    async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
        return await self._run(list(messages), model, **opts)

    async def generate_with_image(
        self, prompt: str, images: list[tuple[bytes, str]], model: str, **opts: object
    ) -> str:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for data, mime in images:
            content.append({"type": "image_url", "image_url": {"url": _data_uri(mime, data)}})
        return await self._run([{"role": "user", "content": content}], model, **opts)
