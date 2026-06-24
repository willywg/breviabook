"""OpenAI provider — also covers OpenAI-compatible endpoints via ``base_url`` (ROADMAP §7.4).

Set ``base_url`` (from ``--api-endpoint``) to target vLLM / LM Studio / LocalAI.
"""

from __future__ import annotations

from brevia.llm.key_pool import KeyPool
from brevia.llm.providers.litellm_base import Completer, LiteLLMProvider


class OpenAIProvider(LiteLLMProvider):
    def __init__(
        self,
        pool: KeyPool,
        *,
        base_url: str | None = None,
        max_retries: int = 3,
        completer: Completer | None = None,
    ) -> None:
        super().__init__(
            name="openai",
            route="openai",
            pool=pool,
            base_url=base_url,
            max_retries=max_retries,
            completer=completer,
        )
