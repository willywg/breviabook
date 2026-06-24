"""Approximate cost estimation from litellm's static price map (ROADMAP §13.5).

Used by ``--dry-run`` to estimate cost without any network call. Returns ``None`` when the
model isn't in litellm's price map (e.g. local Ollama models, or unpriced previews).
"""

from __future__ import annotations


def estimate_cost(
    route: str, model: str, prompt_tokens: int, completion_tokens: int
) -> float | None:
    """Best-effort USD cost for ``prompt_tokens``/``completion_tokens`` on ``route/model``."""
    try:
        import litellm

        prompt_cost, completion_cost = litellm.cost_per_token(
            model=f"{route}/{model}",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        total = float(prompt_cost) + float(completion_cost)
        return total if total > 0 else None
    except Exception:
        return None
