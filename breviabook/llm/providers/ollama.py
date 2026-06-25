"""Ollama provider — local models via litellm (ROADMAP §7.4).

We call ``litellm.acompletion`` with the ``ollama/<model>`` route and the configured
``api_base``. litellm (MIT) gives us a uniform interface and unlocks the cloud providers in
Phase 9 without porting any third-party provider code.
"""

from __future__ import annotations

import base64
from typing import Any, cast

import litellm

from breviabook.llm.base import Message
from breviabook.llm.usage import Usage, extract_usage


class OllamaProvider:
    """Talks to a local Ollama server through litellm."""

    name = "ollama"

    def __init__(self, endpoint: str = "http://localhost:11434") -> None:
        self.endpoint = endpoint
        self.usage = Usage()  # local models are free; cost stays 0

    async def generate(
        self,
        messages: list[Message],
        model: str,
        **opts: object,
    ) -> str:
        """Return the completion text for ``messages`` using local ``model``."""
        response = await litellm.acompletion(
            model=f"ollama/{model}",
            messages=messages,
            api_base=self.endpoint,
            **opts,
        )
        self.usage.add(*extract_usage(response))
        content = response["choices"][0]["message"]["content"]
        return cast(str, content or "")

    async def generate_with_image(
        self, prompt: str, images: list[tuple[bytes, str]], model: str, **opts: object
    ) -> str:
        """Vision completion for a local multimodal model (e.g. qwen3-vl)."""
        parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for data, mime in images:
            uri = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
            parts.append({"type": "image_url", "image_url": {"url": uri}})
        response = await litellm.acompletion(
            model=f"ollama/{model}",
            messages=[{"role": "user", "content": parts}],
            api_base=self.endpoint,
            **opts,
        )
        self.usage.add(*extract_usage(response))
        content = response["choices"][0]["message"]["content"]
        return cast(str, content or "")
