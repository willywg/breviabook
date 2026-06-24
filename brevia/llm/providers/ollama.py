"""Ollama provider — local models via litellm (ROADMAP §7.4).

We call ``litellm.acompletion`` with the ``ollama/<model>`` route and the configured
``api_base``. litellm (MIT) gives us a uniform interface and unlocks the cloud providers in
Phase 9 without porting any third-party provider code.
"""

from __future__ import annotations

from typing import cast

import litellm

from brevia.llm.base import Message
from brevia.llm.usage import Usage, extract_usage


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
