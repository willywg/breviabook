"""LLM provider Protocol — the single abstraction the rest of Brevia depends on.

Nothing outside ``brevia/llm/`` imports ``litellm`` or any provider SDK directly; they all
go through this Protocol. That keeps the pipeline provider-agnostic and the clean-room
boundary intact (ROADMAP §7.4, §14).
"""

from __future__ import annotations

from typing import Protocol, TypedDict, runtime_checkable


class Message(TypedDict):
    """A single chat message in the OpenAI-style ``{"role", "content"}`` shape."""

    role: str
    content: str


@runtime_checkable
class LLMProvider(Protocol):
    """Async chat-completion provider.

    Implementations live in ``brevia/llm/providers/``. ``generate_with_image`` is optional
    and only used by the Phase 11 vision ranker; text-only providers may omit it.
    """

    name: str

    async def generate(
        self,
        messages: list[Message],
        model: str,
        **opts: object,
    ) -> str:
        """Return the assistant's text completion for ``messages`` using ``model``."""
        ...
