"""Base for litellm-backed providers (OpenAI, Gemini, OpenRouter) — ROADMAP §7.4.

Each provider differs only by its litellm route prefix (``openai/``, ``gemini/``,
``openrouter/``) and an optional ``base_url`` (for OpenAI-compatible endpoints). Calls go
through key rotation. litellm is lazy-imported (kept out of import time / test paths); tests
inject a ``completer`` instead.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from brevia.llm.base import Message
from brevia.llm.key_pool import KeyPool
from brevia.llm.rate_limit import with_key_rotation

Completer = Callable[..., Awaitable[Any]]


def _extract_content(response: Any) -> str:
    content = response["choices"][0]["message"]["content"]
    return str(content) if content is not None else ""


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

    async def _acompletion(self, **kwargs: Any) -> Any:
        if self._completer is not None:
            return await self._completer(**kwargs)
        import litellm

        return await litellm.acompletion(**kwargs)

    async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
        async def call(key: str | None) -> str:
            response = await self._acompletion(
                model=f"{self.route}/{model}",
                messages=messages,
                api_key=key,
                api_base=self.base_url,
                **opts,
            )
            return _extract_content(response)

        return await with_key_rotation(self.pool, call, max_retries=self.max_retries)
