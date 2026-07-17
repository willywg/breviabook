"""Shared test fixtures.

``MockProvider`` is the deterministic LLM stand-in used across pipeline tests so we never
call a real model (ROADMAP §11). It satisfies the ``LLMProvider`` Protocol structurally.
"""

from __future__ import annotations

from breviabook.llm.base import Message
from breviabook.llm.usage import Usage


class MockProvider:
    """Deterministic ``LLMProvider`` for tests — echoes a canned reply."""

    name = "mock"

    def __init__(self, reply: str = "MOCK_OK") -> None:
        self.reply = reply
        self.calls: list[tuple[list[Message], str]] = []
        self.usage = Usage()

    async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
        self.calls.append((messages, model))
        return self.reply
