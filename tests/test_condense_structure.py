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


# --------------------------------------------------------------------------- #
# Block-addressed entries — the contract that lets prose merge without losing
# which entry was the list.
# --------------------------------------------------------------------------- #


def _mixed_run() -> list[ListBlock | ParagraphBlock | QuoteBlock]:
    """Two paragraphs, a list, a quote — the shape that used to degrade."""
    return [
        ParagraphBlock(text="First long paragraph."),
        ParagraphBlock(text="Second long paragraph."),
        ListBlock(items=["alpha", "beta"], ordered=False, marker_type="square"),
        QuoteBlock(text="A long citation.", align="center"),
    ]


def test_addressed_entries_may_merge_paragraphs_beside_a_list() -> None:
    """The whole point: blocks 1 and 2 become one entry, the list still lands."""
    out = parse_condensed_run(
        [
            {"block": 1, "type": "paragraph", "text": "Both paragraphs, briefly."},
            {"block": 3, "type": "list", "items": ["a", "b"]},
            {"block": 4, "type": "quote", "text": "Short citation."},
        ],
        _mixed_run(),
    )
    assert [b.type for b in out] == ["paragraph", "list", "quote"]
    lst = out[1]
    assert isinstance(lst, ListBlock)
    assert lst.items == ["a", "b"]
    assert lst.marker_type == "square"  # presentation still comes from the source
    assert isinstance(out[2], QuoteBlock) and out[2].align == "center"


def test_addressed_entries_may_split_one_paragraph() -> None:
    source = [ParagraphBlock(text="One dense paragraph.", anchor_id="sec-1")]
    out = parse_condensed_run(
        [
            {"block": 1, "type": "paragraph", "text": "First half."},
            {"block": 1, "type": "paragraph", "text": "Second half."},
        ],
        source,
    )
    assert [b.text for b in out] == ["First half.", "Second half."]
    # One anchor cannot name two places: only the first piece keeps it.
    assert [b.anchor_id for b in out if isinstance(b, ParagraphBlock)] == ["sec-1", None]


def test_addressed_entry_may_omit_the_type() -> None:
    """The address already determines the type; a missing one is not a violation."""
    out = parse_condensed_run([{"block": 1, "text": "Short."}], [ParagraphBlock(text="Long.")])
    assert [b.text for b in out] == ["Short."]


def test_addressed_entries_accept_a_numeric_string() -> None:
    """Models write "2" as often as 2, and the meaning is not in doubt."""
    out = parse_condensed_run([{"block": "1", "text": "Short."}], [ParagraphBlock(text="Long.")])
    assert [b.text for b in out] == ["Short."]


def test_addressed_run_refuses_to_drop_a_list() -> None:
    """A paragraph may be absorbed by its neighbour; a list has a type to lose."""
    with pytest.raises(CondenseError, match="cannot be dropped"):
        parse_condensed_run([{"block": 1, "text": "Only the prose survived."}], _mixed_run())


def test_addressed_run_refuses_to_flatten_a_list_into_a_paragraph() -> None:
    """Naming the list block does not license answering with prose for it."""
    with pytest.raises(CondenseError, match="expected list block"):
        parse_condensed_run(
            [
                {"block": 1, "type": "paragraph", "text": "Prose."},
                {"block": 3, "type": "paragraph", "text": "alpha and beta"},
                {"block": 4, "type": "quote", "text": "Citation."},
            ],
            _mixed_run(),
        )


def test_addressed_run_refuses_to_split_a_list() -> None:
    with pytest.raises(CondenseError, match="cannot be split"):
        parse_condensed_run(
            [
                {"block": 1, "type": "paragraph", "text": "Prose."},
                {"block": 3, "type": "list", "items": ["a"]},
                {"block": 3, "type": "list", "items": ["b"]},
                {"block": 4, "type": "quote", "text": "Citation."},
            ],
            _mixed_run(),
        )


def test_addressed_run_refuses_reordering() -> None:
    """Reordering prose rewrites the argument the author made."""
    source = [ParagraphBlock(text="First."), ParagraphBlock(text="Second.")]
    with pytest.raises(CondenseError, match="must ascend"):
        parse_condensed_run(
            [
                {"block": 2, "type": "paragraph", "text": "Second, briefly."},
                {"block": 1, "type": "paragraph", "text": "First, briefly."},
            ],
            source,
        )


def test_addressed_run_refuses_a_block_number_that_does_not_exist() -> None:
    with pytest.raises(CondenseError, match="but this run has 1"):
        parse_condensed_run([{"block": 7, "text": "Short."}], [ParagraphBlock(text="Long.")])


def test_half_addressed_array_falls_back_to_positional_reading() -> None:
    """A model that lost the contract mid-response gets read the old way.

    Guessing which half to trust would be worse: here the counts still line up,
    so the positional reading is well defined and nothing is lost.
    """
    source = [ParagraphBlock(text="First."), ListBlock(items=["a"])]
    out = parse_condensed_run(
        [{"block": 1, "type": "paragraph", "text": "Short."}, {"type": "list", "items": ["b"]}],
        source,
    )
    assert [b.type for b in out] == ["paragraph", "list"]


def test_positional_array_still_works_unaddressed() -> None:
    """The old form stays valid — this change may only ever add a way to answer."""
    source = [ParagraphBlock(text="Intro."), ListBlock(items=["a", "b"])]
    out = parse_condensed_run(
        [{"type": "paragraph", "text": "Short."}, {"type": "list", "items": ["one"]}], source
    )
    assert [b.type for b in out] == ["paragraph", "list"]


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
