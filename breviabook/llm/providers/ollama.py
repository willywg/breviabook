"""Ollama provider — local models via litellm (ROADMAP §7.4).

We call ``litellm.acompletion`` with the ``ollama/<model>`` route and the configured
``api_base``. litellm (MIT) gives us a uniform interface and unlocks the cloud providers in
Phase 9 without porting any third-party provider code.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from typing import Any

from breviabook.llm.base import Message
from breviabook.llm.rate_limit import retry_with_backoff
from breviabook.llm.usage import Usage, extract_usage

Completer = Callable[..., Awaitable[Any]]


def _extract_content(response: Any) -> str:
    content = response["choices"][0]["message"]["content"]
    return str(content) if content is not None else ""


class OllamaProvider:
    """Talks to a local Ollama server through litellm."""

    name = "ollama"

    def __init__(
        self,
        endpoint: str = "http://localhost:11434",
        *,
        max_retries: int = 3,
        completer: Completer | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.endpoint = endpoint
        self.max_retries = max_retries
        self._completer = completer
        self._sleep = sleep
        self.usage = Usage()  # local models are free; cost stays 0

    async def _acompletion(self, **kwargs: Any) -> Any:
        if self._completer is not None:
            return await self._completer(**kwargs)
        import litellm

        return await litellm.acompletion(**kwargs)

    async def _run(self, messages: list[Any], model: str, **opts: object) -> str:
        async def call() -> str:
            response = await self._acompletion(
                model=f"ollama/{model}",
                messages=messages,
                api_base=self.endpoint,
                **opts,
            )
            self.usage.add(*extract_usage(response))
            return _extract_content(response)

        return await retry_with_backoff(
            call,
            max_retries=self.max_retries,
            sleep=self._sleep,
        )

    async def generate(
        self,
        messages: list[Message],
        model: str,
        **opts: object,
    ) -> str:
        """Return the completion text for ``messages`` using local ``model``."""
        return await self._run(list(messages), model, **opts)

    async def generate_with_image(
        self, prompt: str, images: list[tuple[bytes, str]], model: str, **opts: object
    ) -> str:
        """Vision completion for a local multimodal model (e.g. qwen3-vl)."""
        parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for data, mime in images:
            uri = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
            parts.append({"type": "image_url", "image_url": {"url": uri}})
        return await self._run([{"role": "user", "content": parts}], model, **opts)
