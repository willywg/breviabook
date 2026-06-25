"""Chunker: chapter-bounded, code-safe, ~max_tokens grouping."""

from __future__ import annotations

from pathlib import Path

from breviabook.condense.chunker import Chunker, count_document_tokens
from breviabook.ir.models import (
    Chapter,
    CodeBlock,
    Document,
    DocumentMetadata,
    ParagraphBlock,
)
from breviabook.parsers.epub_parser import EpubParser
from breviabook.utils.tokens import block_tokens, count_tokens

FIXTURE = Path(__file__).parent / "fixtures" / "sample.epub"


def _doc(chapters: list[Chapter]) -> Document:
    return Document(metadata=DocumentMetadata(title="T", source_format="epub"), chapters=chapters)


def test_chunks_never_cross_chapters() -> None:
    doc = _doc(
        [
            Chapter(title="A", blocks=[ParagraphBlock(text="a1"), ParagraphBlock(text="a2")]),
            Chapter(title="B", blocks=[ParagraphBlock(text="b1")]),
        ]
    )
    chunks = Chunker(max_tokens=2000).chunk(doc)
    by_chapter = {c.chapter_index for c in chunks}
    assert by_chapter == {0, 1}
    for c in chunks:
        assert all(isinstance(b, ParagraphBlock) for b in c.blocks)


def test_grouping_respects_max_tokens() -> None:
    # Each paragraph ~ a few tokens; with a tiny budget every block is its own chunk.
    blocks = [ParagraphBlock(text=f"word{i} more text here") for i in range(5)]
    doc = _doc([Chapter(title="A", blocks=blocks)])
    chunks = Chunker(max_tokens=4).chunk(doc)
    assert len(chunks) == 5
    assert [c.id for c in chunks] == [f"ch0-{i}" for i in range(1, 6)]


def test_code_block_never_split_and_stays_intact() -> None:
    code = "print('x')\n" * 200  # large code block
    doc = _doc(
        [
            Chapter(
                title="A",
                blocks=[
                    ParagraphBlock(text="intro"),
                    CodeBlock(language="python", text=code),
                    ParagraphBlock(text="outro"),
                ],
            )
        ]
    )
    chunks = Chunker(max_tokens=10).chunk(doc)
    # The oversized code block is its own chunk, byte-for-byte intact.
    code_chunks = [c for c in chunks if any(isinstance(b, CodeBlock) for b in c.blocks)]
    assert len(code_chunks) == 1
    code_block = next(b for b in code_chunks[0].blocks if isinstance(b, CodeBlock))
    assert code_block.text == code


def test_token_count_matches_blocks() -> None:
    blocks = [ParagraphBlock(text="alpha beta"), ParagraphBlock(text="gamma delta")]
    doc = _doc([Chapter(title="A", blocks=blocks)])
    chunk = Chunker(max_tokens=2000).chunk(doc)[0]
    assert chunk.token_count == sum(block_tokens(b) for b in blocks)


def test_prev_context_only_after_first_chunk() -> None:
    blocks = [ParagraphBlock(text=f"sentence number {i} here") for i in range(3)]
    doc = _doc([Chapter(title="A", blocks=blocks)])
    chunks = Chunker(max_tokens=4).chunk(doc)
    assert chunks[0].prev_context is None
    assert chunks[1].prev_context is not None
    assert "sentence number 0" in chunks[1].prev_context


def test_chunks_the_real_fixture() -> None:
    doc = EpubParser().parse(FIXTURE)
    chunks = Chunker(max_tokens=2000).chunk(doc)
    assert chunks
    assert {c.chapter_index for c in chunks} == {0, 1}


def test_count_document_tokens_positive() -> None:
    doc = EpubParser().parse(FIXTURE)
    assert count_document_tokens(doc) > 0
    assert isinstance(count_tokens("x"), int)
