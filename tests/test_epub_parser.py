"""Phase 1 acceptance tests: EPUB -> IR loses no blocks or images (ROADMAP §11)."""

from __future__ import annotations

from pathlib import Path

import pytest

from breviabook.ir.models import (
    CodeBlock,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    ParagraphBlock,
    QuoteBlock,
    TableBlock,
)
from breviabook.parsers.epub_parser import EpubParser
from breviabook.utils.security import resolve_archive_href

FIXTURE = Path(__file__).parent / "fixtures" / "sample.epub"


@pytest.fixture
def doc():
    return EpubParser().parse(FIXTURE)


def test_metadata(doc) -> None:
    assert doc.metadata.title == "BreviaBook Sample Book"
    assert doc.metadata.author == "William Wong Garay"
    assert doc.metadata.language == "en"
    assert doc.metadata.source_format == "epub"


def test_chapter_count_and_titles(doc) -> None:
    assert len(doc.chapters) == 2
    assert [c.title for c in doc.chapters] == ["Chapter One", "Chapter Two"]


def test_all_block_types_present(doc) -> None:
    kinds = {b.type for _, b in doc.iter_blocks()}
    assert kinds == {"heading", "paragraph", "code", "image", "table", "quote", "list"}


def test_code_block_preserved_verbatim(doc) -> None:
    code = next(b for _, b in doc.iter_blocks() if isinstance(b, CodeBlock))
    assert code.language == "python"
    assert code.text == 'def hello() -> str:\n    return "world"\n'


def test_images_extracted_once_with_unique_ids(doc) -> None:
    assert set(doc.images) == {"fig1"}
    asset = doc.images["fig1"]
    assert asset.mime == "image/png"
    assert asset.data.startswith(b"\x89PNG")
    assert asset.original_path == "OEBPS/images/fig1.png"
    refs = [b for _, b in doc.iter_blocks() if isinstance(b, ImageBlock)]
    assert len(refs) == 1
    assert refs[0].image_id == "fig1"
    assert refs[0].caption == "Figure 2.1 - the architecture"


def test_no_orphan_image_refs(doc) -> None:
    for _, block in doc.iter_blocks():
        if isinstance(block, ImageBlock):
            assert block.image_id in doc.images


def test_structural_block_details(doc) -> None:
    blocks = [b for _, b in doc.iter_blocks()]
    assert any(isinstance(b, HeadingBlock) and b.level == 1 for b in blocks)
    assert any(isinstance(b, ParagraphBlock) for b in blocks)
    assert any(isinstance(b, QuoteBlock) for b in blocks)
    lst = next(b for b in blocks if isinstance(b, ListBlock))
    assert lst.items == ["First item", "Second item"]
    table = next(b for b in blocks if isinstance(b, TableBlock))
    assert table.rows == [["Name", "Value"], ["alpha", "1"]]


def test_zip_slip_href_rejected() -> None:
    with pytest.raises(ValueError, match="zip-slip"):
        resolve_archive_href("OEBPS/content.opf", "../../etc/passwd")


def test_safe_href_resolves() -> None:
    assert resolve_archive_href("OEBPS/content.opf", "images/fig1.png") == "OEBPS/images/fig1.png"
