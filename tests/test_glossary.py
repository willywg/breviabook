"""Glossary: term map loading and prompt rendering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from breviabook.translate.glossary import Glossary


def test_empty_glossary_is_falsy_and_blank_prompt() -> None:
    g = Glossary({})
    assert not g
    assert g.prompt_block() == ""


def test_prompt_block_lists_terms() -> None:
    g = Glossary({"thread": "hilo", "callback": "callback"})
    block = g.prompt_block()
    assert "thread → hilo" in block
    assert "callback → callback" in block


def test_from_json(tmp_path: Path) -> None:
    path = tmp_path / "g.json"
    path.write_text(json.dumps({"thread": "hilo"}), encoding="utf-8")
    g = Glossary.from_json(path)
    assert g.terms == {"thread": "hilo"}


def test_from_json_rejects_non_object(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(["not", "a", "map"]), encoding="utf-8")
    with pytest.raises(ValueError):
        Glossary.from_json(path)
