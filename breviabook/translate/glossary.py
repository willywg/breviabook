"""Translation glossary — term consistency (ROADMAP §2.1, §7.5).

A user-provided map of ``source_term -> target_term`` (JSON) injected into the translation
prompt so technical terms translate consistently (e.g. "thread" doesn't become five different
words). NER-based auto-extraction (spacy) is a later, optional enhancement.
"""

from __future__ import annotations

import json
from pathlib import Path


class Glossary:
    """A source→target term map used to keep translations consistent."""

    def __init__(self, terms: dict[str, str]) -> None:
        self.terms = {str(k): str(v) for k, v in terms.items() if str(k).strip()}

    def __bool__(self) -> bool:
        return bool(self.terms)

    @classmethod
    def from_json(cls, path: Path) -> Glossary:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Glossary must be a JSON object of {source_term: target_term}")
        return cls(data)

    def prompt_block(self) -> str:
        """Render the glossary as an instruction block (empty string if no terms)."""
        if not self.terms:
            return ""
        lines = "\n".join(f"- {src} → {tgt}" for src, tgt in self.terms.items())
        return f"Use these exact term translations consistently:\n{lines}\n"
