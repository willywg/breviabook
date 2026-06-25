"""Parser abstraction — every input format produces a ``Document`` (ROADMAP §3)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from breviabook.ir.models import Document


class ParseError(Exception):
    """Raised when a source file cannot be parsed into the IR."""


class Parser(Protocol):
    """Turns a source file into the IR. Implementations: EPUB (Phase 1), PDF (Phase 8)."""

    def parse(self, path: Path) -> Document:
        """Parse ``path`` into a :class:`~breviabook.ir.models.Document`."""
        ...
