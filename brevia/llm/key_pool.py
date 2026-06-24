"""API-key rotation pool (ROADMAP §7.4, §9).

A simple round-robin over the comma-separated keys configured for a provider. Rotation
(advance to the next key) is driven by :mod:`brevia.llm.rate_limit` on auth/rate-limit
failures. Reimplemented clean-room from the TBL pattern (§14) — no copied code.
"""

from __future__ import annotations


class KeyPool:
    """Round-robin pool of API keys."""

    def __init__(self, keys: list[str]) -> None:
        self._keys = [k.strip() for k in keys if k and k.strip()]
        self._index = 0

    def __bool__(self) -> bool:
        return bool(self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    @property
    def current(self) -> str | None:
        return self._keys[self._index] if self._keys else None

    def rotate(self) -> str | None:
        """Advance to the next key and return it (wraps around)."""
        if not self._keys:
            return None
        self._index = (self._index + 1) % len(self._keys)
        return self.current
