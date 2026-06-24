"""Token counting + block-text extraction."""

from __future__ import annotations

from brevia.ir.models import (
    CodeBlock,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    ParagraphBlock,
    QuoteBlock,
    TableBlock,
)
from brevia.utils.tokens import block_text, block_tokens, count_tokens


def test_count_tokens_empty_is_zero() -> None:
    assert count_tokens("") == 0


def test_count_tokens_monotonic_and_positive() -> None:
    short = count_tokens("hello world")
    long = count_tokens("hello world " * 50)
    assert short > 0
    assert long > short


def test_block_text_per_kind() -> None:
    assert block_text(HeadingBlock(level=1, text="Title")) == "Title"
    assert block_text(ParagraphBlock(text="para")) == "para"
    assert block_text(QuoteBlock(text="q")) == "q"
    assert block_text(CodeBlock(text="x = 1")) == "x = 1"
    assert block_text(ListBlock(items=["a", "b"])) == "a\nb"
    assert block_text(TableBlock(rows=[["a", "b"], ["c", "d"]])) == "a b\nc d"
    assert block_text(ImageBlock(image_id="i", caption="cap")) == "cap"
    assert block_text(ImageBlock(image_id="i")) == ""


def test_block_tokens_matches_count_tokens() -> None:
    block = ParagraphBlock(text="some words here for counting")
    assert block_tokens(block) == count_tokens("some words here for counting")
