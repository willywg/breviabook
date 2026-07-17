"""LLM provider Protocol — the single abstraction the rest of BreviaBook depends on.

Nothing outside ``breviabook/llm/`` imports ``litellm`` or any provider SDK directly; they all
go through this Protocol. That keeps the pipeline provider-agnostic and the clean-room
boundary intact (ROADMAP §7.4, §14).
"""

from __future__ import annotations

from typing import Protocol, TypedDict, runtime_checkable

from breviabook.llm.usage import Usage


class Message(TypedDict):
    """A single chat message in the OpenAI-style ``{"role", "content"}`` shape."""

    role: str
    content: str


@runtime_checkable
class LLMProvider(Protocol):
    """Async chat-completion provider.

    Implementations live in ``breviabook/llm/providers/``. Every provider exposes a live
    :class:`~breviabook.llm.usage.Usage` accumulator. ``generate_with_image`` is optional
    and only used by the Phase 11 vision ranker; text-only providers may omit it.
    """

    name: str
    usage: Usage

    async def generate(
        self,
        messages: list[Message],
        model: str,
        **opts: object,
    ) -> str:
        """Return the assistant's text completion for ``messages`` using ``model``."""
        ...


@runtime_checkable
class VisionProvider(Protocol):
    """A provider that can reason over images (for the Phase 11 vision ranker)."""

    async def generate_with_image(
        self,
        prompt: str,
        images: list[tuple[bytes, str]],
        model: str,
        **opts: object,
    ) -> str:
        """Return a completion for ``prompt`` plus ``images`` (each ``(bytes, mime)``)."""
        ...
