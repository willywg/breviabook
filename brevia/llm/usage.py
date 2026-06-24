"""LLM usage & cost accounting.

Providers accumulate a :class:`Usage` from each response's ``usage`` block (prompt /
completion / cached tokens) and an approximate cost. Cached tokens cover prompt-cache hits
(e.g. Gemini context caching), reported separately so savings are visible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Usage:
    """Accumulated token usage and approximate cost across LLM calls."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    calls: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, prompt: int, completion: int, cached: int, cost: float = 0.0) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.cached_tokens += cached
        self.cost_usd += cost
        self.calls += 1


def _field(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def extract_usage(response: Any) -> tuple[int, int, int]:
    """Return ``(prompt_tokens, completion_tokens, cached_tokens)`` from an LLM response.

    Works for both plain dicts (tests) and litellm's response objects.
    """
    usage = _field(response, "usage")
    prompt = int(_field(usage, "prompt_tokens") or 0)
    completion = int(_field(usage, "completion_tokens") or 0)
    details = _field(usage, "prompt_tokens_details")
    cached = int(_field(details, "cached_tokens") or 0)
    return prompt, completion, cached
