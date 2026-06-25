"""Cost estimation from litellm's static price map (no network)."""

from __future__ import annotations

from breviabook.llm.pricing import estimate_cost


def test_local_model_has_no_price() -> None:
    assert estimate_cost("ollama", "gemma4:e4b", 1000, 1000) is None


def test_bogus_model_is_none() -> None:
    assert estimate_cost("openai", "totally-not-a-real-model-xyz", 10, 10) is None


def test_known_model_returns_positive_cost() -> None:
    cost = estimate_cost("openai", "gpt-4o-mini", 1_000_000, 1_000_000)
    assert cost is not None and cost > 0
