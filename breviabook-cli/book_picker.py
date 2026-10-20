"""Book selection through the common multi-choice terminal picker."""

from __future__ import annotations

from pickers import select_many


def select_books(labels: list[str]) -> list[int] | None:
    """Return selected indices; empty means rescan, None means quit."""
    return select_many("Books to process", labels)
