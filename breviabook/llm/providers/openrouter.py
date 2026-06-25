"""OpenRouter provider (litellm ``openrouter/`` route) — ROADMAP §7.4."""

from __future__ import annotations

from breviabook.llm.key_pool import KeyPool
from breviabook.llm.providers.litellm_base import Completer, LiteLLMProvider


class OpenRouterProvider(LiteLLMProvider):
    def __init__(
        self, pool: KeyPool, *, max_retries: int = 3, completer: Completer | None = None
    ) -> None:
        super().__init__(
            name="openrouter",
            route="openrouter",
            pool=pool,
            max_retries=max_retries,
            completer=completer,
        )
