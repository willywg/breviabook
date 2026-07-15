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


def _epub_with_styling(path: Path) -> None:
    """Write a minimal EPUB whose content mixes class-color, italic, a link, and plain text."""
    import zipfile

    container = (
        '<?xml version="1.0"?><container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
        '<rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="b"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="b">x</dc:identifier><dc:title>T</dc:title>'
        "<dc:language>en</dc:language></metadata><manifest>"
        '<item id="css" href="s.css" media-type="text/css"/>'
        '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
        '</manifest><spine><itemref idref="c1"/></spine></package>'
    )
    css = ".red { color: #9e0b0f } .ital { font-style: italic }"
    ch = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head>'
        "<title>C</title></head><body>"
        '<h2><span class="red">CHAPTER 1</span> <b>Don\'t</b> think</h2>'
        '<p>A <span class="ital">plain</span> mix with '
        '<a href="https://x.com">a link</a>.</p>'
        "<p>Totally plain paragraph.</p>"
        "</body></html>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/s.css", css)
        zf.writestr("OEBPS/c1.xhtml", ch)


def test_inline_formatting_extracted_to_rich(tmp_path) -> None:
    epub = tmp_path / "styled.epub"
    _epub_with_styling(epub)
    doc = EpubParser().parse(epub)
    blocks = [b for _, b in doc.iter_blocks()]

    heading = next(b for b in blocks if isinstance(b, HeadingBlock))
    assert heading.text == "CHAPTER 1 Don't think"  # plain projection preserved
    assert heading.rich is not None
    assert "color:#9e0b0f" in heading.rich and "<strong>Don" in heading.rich

    paras = [b for b in blocks if isinstance(b, ParagraphBlock)]
    styled = next(b for b in paras if b.rich is not None)
    assert "<em>plain</em>" in styled.rich
    assert '<a href="https://x.com">a link</a>' in styled.rich

    plain = next(b for b in paras if b.text == "Totally plain paragraph.")
    assert plain.rich is None  # no markup → stays simple
