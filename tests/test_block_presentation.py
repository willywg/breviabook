"""Block presentation: align + list markers (feat--block-fidelity Phase A)."""

from __future__ import annotations

import zipfile
from pathlib import Path

from breviabook.ir.models import (
    Chapter,
    Document,
    DocumentMetadata,
    HeadingBlock,
    ListBlock,
    ParagraphBlock,
    QuoteBlock,
)
from breviabook.parsers.epub_parser import EpubParser
from breviabook.render.html import block_to_html
from breviabook.render.md_renderer import MarkdownRenderer


def _noop_src(_image_id: str) -> str | None:
    return None


def _epub_with_presentation(path: Path) -> None:
    """Minimal EPUB: centered attribution (wrapper + direct), square red bullets."""
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
    css = ".center { text-align: center } .redsquare { list-style-type: square; color: #c00 }"
    ch = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head>'
        "<title>C</title></head><body>"
        "<h1>Chapter</h1>"
        '<div class="center"><p>— Steve Krug, 2000</p></div>'
        '<p style="text-align:center">2000</p>'
        '<blockquote class="center">A memorable quote.</blockquote>'
        '<ul class="redsquare"><li>First</li><li>Second</li></ul>'
        '<div class="center"><p>One</p><p>Two</p></div>'
        "</body></html>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/s.css", css)
        zf.writestr("OEBPS/c1.xhtml", ch)


def test_parser_extracts_align_and_list_markers(tmp_path: Path) -> None:
    epub = tmp_path / "pres.epub"
    _epub_with_presentation(epub)
    doc = EpubParser().parse(epub)
    blocks = [b for _, b in doc.iter_blocks()]

    attribution = next(b for b in blocks if isinstance(b, ParagraphBlock) and "Steve" in b.text)
    assert attribution.align == "center"

    year = next(b for b in blocks if isinstance(b, ParagraphBlock) and b.text == "2000")
    assert year.align == "center"

    quote = next(b for b in blocks if isinstance(b, QuoteBlock))
    assert quote.align == "center"

    lst = next(b for b in blocks if isinstance(b, ListBlock))
    assert lst.marker_type == "square"
    assert lst.marker_color == "#c00"

    # Wrapper with two block children must NOT inherit align onto either.
    multi = [b for b in blocks if isinstance(b, ParagraphBlock) and b.text in {"One", "Two"}]
    assert len(multi) == 2
    assert all(b.align is None for b in multi)


def test_html_emits_align_and_marker_via_marker_pseudo() -> None:
    centered = block_to_html(ParagraphBlock(text="x", align="center"), _noop_src)
    assert centered == '<p style="text-align:center">x</p>'

    quote = block_to_html(QuoteBlock(text="q", align="center"), _noop_src)
    assert 'style="text-align:center"' in quote

    heading = block_to_html(HeadingBlock(level=2, text="H", align="right"), _noop_src)
    assert heading.startswith('<h2 style="text-align:right">')

    lst = block_to_html(
        ListBlock(items=["a", "b"], marker_type="square", marker_color="#c00"),
        _noop_src,
    )
    assert 'style="list-style-type:square"' in lst
    assert "li::marker" in lst
    assert "color:#c00" in lst
    # Never set color on the list element itself (would bleed into li text).
    assert "<ul" in lst
    assert 'color:#c00"' not in lst.split("<ul", 1)[1].split(">", 1)[0]
    assert 'style="color' not in lst.split("<ul", 1)[1].split(">", 1)[0]


def test_md_ignores_block_presentation(tmp_path: Path) -> None:
    doc = Document(
        metadata=DocumentMetadata(title="T", source_format="epub"),
        chapters=[
            Chapter(
                blocks=[
                    ParagraphBlock(text="Centered", align="center"),
                    ListBlock(items=["a"], marker_type="square", marker_color="#c00"),
                ]
            )
        ],
    )
    out = MarkdownRenderer().render(doc, tmp_path, stem="m")
    md = out.read_text(encoding="utf-8")
    assert "Centered" in md
    assert "- a" in md
    assert "text-align" not in md
    assert "list-style" not in md
    assert "::marker" not in md
