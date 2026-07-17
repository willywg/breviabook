"""Shared content/config fingerprint for checkpoint records (ROADMAP §5, §13.4).

Both checkpointed pipelines (condense, translate) stamp every record with a SHA-1
fingerprint of the inputs that determine the output — model, chunking, ratio, languages,
glossary, content — so a ``--resume`` after changing any of them recomputes stale records
instead of silently reusing them.
"""

from __future__ import annotations

import hashlib


class Fingerprint:
    """Incremental SHA-1 over NUL-separated UTF-8 fields.

    NUL separation matters: without it, the field sequences ``("ab", "c")`` and
    ``("a", "bc")`` would hash to the same digest.
    """

    def __init__(self) -> None:
        self._h = hashlib.sha1(usedforsecurity=False)

    def field(self, value: str) -> None:
        self._h.update(value.encode("utf-8"))
        self._h.update(b"\0")

    def hexdigest(self) -> str:
        return self._h.hexdigest()
