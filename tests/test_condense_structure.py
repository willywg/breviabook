"""Block-aligned condense parsing and serialization (no LLM)."""

from __future__ import annotations

import pytest

from breviabook.condense.common import (
    CondenseError,
    parse_condensed_run,
    parse_run_or_keep,
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


def test_parse_condensed_run_copies_presentation_shell() -> None:
    source = [
        ParagraphBlock(text="Long centered attribution.", align="center"),
        ListBlock(
            items=["First detailed.", "Second detailed."],
            ordered=False,
            marker_type="square",
            marker_color="#c00",
        ),
        QuoteBlock(text="A long citation.", align="center"),
    ]
    raw = [
        {"type": "paragraph", "text": "Short."},
        {"type": "list", "items": ["One.", "Two."]},
        {"type": "quote", "text": "Quote."},
    ]
    out = parse_condensed_run(raw, source)
    assert isinstance(out[0], ParagraphBlock) and out[0].align == "center"
    lst = out[1]
    assert isinstance(lst, ListBlock)
    assert lst.marker_type == "square" and lst.marker_color == "#c00"
    assert isinstance(out[2], QuoteBlock) and out[2].align == "center"


def test_parse_condensed_run_string_on_structured_run_raises() -> None:
    source = [ListBlock(items=["a"]), ParagraphBlock(text="p")]
    with pytest.raises(CondenseError, match="array response"):
        parse_condensed_run("flat prose", source)


def test_parse_condensed_run_count_mismatch_raises() -> None:
    source = [ParagraphBlock(text="a"), QuoteBlock(text="q")]
    with pytest.raises(CondenseError, match="block count mismatch"):
        parse_condensed_run([{"type": "paragraph", "text": "only one"}], source)


def test_paragraph_only_run_may_merge_blocks() -> None:
    """Condensing prose merges paragraphs. A run of only paragraphs must allow it."""
    source = [ParagraphBlock(text="First long."), ParagraphBlock(text="Second long.")]
    out = parse_condensed_run([{"type": "paragraph", "text": "Both, briefly."}], source)
    assert len(out) == 1
    assert isinstance(out[0], ParagraphBlock) and out[0].text == "Both, briefly."


def test_paragraph_only_run_may_split_blocks() -> None:
    source = [ParagraphBlock(text="One dense paragraph.")]
    out = parse_condensed_run(["First half.", "Second half."], source)
    assert [b.text for b in out] == ["First half.", "Second half."]


def test_structured_run_still_demands_alignment() -> None:
    """A list or quote has a type to preserve, so its run keeps the strict rule."""
    source = [ParagraphBlock(text="p"), ListBlock(items=["a", "b"])]
    with pytest.raises(CondenseError, match="block count mismatch"):
        parse_condensed_run([{"type": "paragraph", "text": "merged"}], source)


def test_missing_entry_raises_rather_than_deleting_prose() -> None:
    """No key for a run means the model never spoke about it — not 'delete it'."""
    source = [ParagraphBlock(text="Text the user wrote.")]
    with pytest.raises(CondenseError, match="no entry returned"):
        parse_condensed_run(None, source)


def test_explicit_empty_string_still_drops_a_paragraph_run() -> None:
    """An empty value is the model saying 'nothing survives', which is legitimate."""
    assert parse_condensed_run("", [ParagraphBlock(text="filler")]) == []


def test_empty_array_drops_a_paragraph_run_like_an_empty_string() -> None:
    """Deliberate: `[]` and `""` are the same statement in two notations.

    Distinguishing them would mean one spelling of "nothing survives" is honoured
    and the other is an error, which no prompt asks the model to know.
    """
    assert parse_condensed_run([], [ParagraphBlock(text="filler")]) == []


def test_parse_run_or_keep_falls_back_to_source() -> None:
    source = [ParagraphBlock(text="p"), QuoteBlock(text="q")]
    blocks, degraded = parse_run_or_keep([{"type": "paragraph", "text": "merged"}], source)
    assert degraded is True
    assert blocks == source


def test_parse_run_or_keep_reports_success() -> None:
    source = [ParagraphBlock(text="long")]
    blocks, degraded = parse_run_or_keep("short", source)
    assert degraded is False
    assert [b.text for b in blocks] == ["short"]
