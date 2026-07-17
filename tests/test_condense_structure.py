"""Block-aligned condense parsing and serialization (no LLM)."""

from __future__ import annotations

import pytest

from breviabook.condense.common import (
    CondenseError,
    parse_condensed_run,
    run_has_structured_blocks,
    serialize_run,
)
from breviabook.ir.models import ListBlock, ParagraphBlock, QuoteBlock


def test_run_has_structured_blocks() -> None:
    assert not run_has_structured_blocks([ParagraphBlock(text="a")])
    assert run_has_structured_blocks([ParagraphBlock(text="a"), ListBlock(items=["x"])])
    assert run_has_structured_blocks([QuoteBlock(text="q")])


def test_serialize_run_labels_block_types() -> None:
    body = serialize_run(
        [
            ParagraphBlock(text="Intro."),
            ListBlock(items=["a", "b"], ordered=True),
            QuoteBlock(text="Quoted."),
        ]
    )
    assert "[BLOCK 1 type=paragraph]" in body
    assert "[BLOCK 2 type=list ordered=true]" in body
    assert "1. a" in body
    assert "[BLOCK 3 type=quote]" in body


def test_parse_condensed_run_paragraph_string() -> None:
    source = [ParagraphBlock(text="long intro filler.")]
    out = parse_condensed_run("Short.\n\nAlso short.", source)
    assert len(out) == 2
    assert all(isinstance(b, ParagraphBlock) for b in out)


def test_parse_condensed_run_structured_array() -> None:
    source = [
        ParagraphBlock(text="Intro filler."),
        ListBlock(items=["First.", "Second."], ordered=False),
        QuoteBlock(text="Citation."),
    ]
    raw = [
        {"type": "paragraph", "text": "Short intro."},
        {"type": "list", "items": ["One.", "Two."]},
        {"type": "quote", "text": "Quote."},
    ]
    out = parse_condensed_run(raw, source)
    assert [b.type for b in out] == ["paragraph", "list", "quote"]
    lst = out[1]
    assert isinstance(lst, ListBlock) and lst.items == ["One.", "Two."]
    assert isinstance(out[2], QuoteBlock)


def test_parse_condensed_run_string_on_structured_run_raises() -> None:
    source = [ListBlock(items=["a"]), ParagraphBlock(text="p")]
    with pytest.raises(CondenseError, match="array response"):
        parse_condensed_run("flat prose", source)


def test_parse_condensed_run_count_mismatch_raises() -> None:
    source = [ParagraphBlock(text="a"), QuoteBlock(text="q")]
    with pytest.raises(CondenseError, match="block count mismatch"):
        parse_condensed_run([{"type": "paragraph", "text": "only one"}], source)
