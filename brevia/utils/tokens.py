"""Token counting and block-text extraction (ROADMAP §7.2).

Counting uses ``tiktoken`` (``cl100k_base``) as a model-agnostic estimate for chunk
budgeting, with a character-based fallback if the encoding can't be loaded. The exact LLM's
tokenizer may differ slightly — that's fine, this drives chunk sizing, not billing.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, assert_never

from brevia.ir.models import (
    Block,
    CodeBlock,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    ParagraphBlock,
    QuoteBlock,
    TableBlock,
)

if TYPE_CHECKING:
    import tiktoken


@lru_cache(maxsize=1)
def _encoding() -> tiktoken.Encoding | None:
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def count_tokens(text: str) -> int:
    """Estimate the number of tokens in ``text``."""
    if not text:
        return 0
    enc = _encoding()
    if enc is not None:
        return len(enc.encode(text))
    return max(1, len(text) // 4)


def block_text(block: Block) -> str:
    """Return the textual content of ``block`` for token budgeting / context."""
    if isinstance(block, (HeadingBlock, ParagraphBlock, QuoteBlock)):
        return block.text
    if isinstance(block, CodeBlock):
        return block.text
    if isinstance(block, ListBlock):
        return "\n".join(block.items)
    if isinstance(block, TableBlock):
        return "\n".join(" ".join(row) for row in block.rows)
    if isinstance(block, ImageBlock):
        return block.caption or ""
    assert_never(block)


def block_tokens(block: Block) -> int:
    """Estimate the token cost of ``block``."""
    return count_tokens(block_text(block))
