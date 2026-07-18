"""F1 / Phase B — opaque bbref remap for in-book cross-references."""

from __future__ import annotations

import zipfile
from pathlib import Path

from breviabook.ir.models import (
    Chapter,
    Document,
    DocumentMetadata,
    HeadingBlock,
    ParagraphBlock,
)
from breviabook.parsers.epub_parser import EpubParser
from breviabook.render.epub_renderer import EpubRenderer
from breviabook.render.html import block_to_html
from breviabook.render.md_renderer import MarkdownRenderer
from breviabook.utils import htmlsan
from breviabook.utils.htmlsan import sanitize_inline, strip_tags


def _mini_epub_with_toc_and_footnote(path: Path) -> None:
    container = (
        '<?xml version="1.0"?><container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
        '<rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="b"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="b">x</dc:identifier><dc:title>Refs</dc:title>'
        "<dc:language>en</dc:language></metadata><manifest>"
        '<item id="toc" href="ch-toc.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="body" href="ch-body.xhtml" media-type="application/xhtml+xml"/>'
        "</manifest><spine>"
        '<itemref idref="toc"/><itemref idref="body"/>'
        "</spine></package>"
    )
    toc = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>'
        "<h1>Contents</h1>"
        '<p><a href="ch-body.xhtml#sec-1"><strong>About this edition</strong></a></p>'
        '<p><a href="ch-body.xhtml">Chapter body</a></p>'
        "</body></html>"
    )
    body = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>'
        '<h2 id="sec-1">Section</h2>'
        '<p>See note <a href="#fn1"><sup>1</sup></a>.</p>'
        '<p id="fn1">Footnote body.</p>'
        "</body></html>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/ch-toc.xhtml", toc)
        zf.writestr("OEBPS/ch-body.xhtml", body)


def test_safe_link_schemes_unchanged() -> None:
    assert htmlsan._SAFE_LINK_SCHEMES == ("http://", "https://", "mailto:")


def test_sanitize_bbref_and_rejects_internal_forms() -> None:
    assert sanitize_inline('<a href="bbref:a1">t</a>') == '<a href="bbref:a1">t</a>'
    assert sanitize_inline('<a href="#x">t</a>') == "t"
    assert sanitize_inline('<a href="ch-body.xhtml#sec-1">t</a>') == "t"
    assert sanitize_inline('<a href="javascript:evil()">t</a>') == "t"
    assert (
        sanitize_inline('<a href="https://example.com">t</a>')
        == '<a href="https://example.com">t</a>'
    )


def test_parse_rewrites_to_bbref_only(tmp_path: Path) -> None:
    epub = tmp_path / "refs.epub"
    _mini_epub_with_toc_and_footnote(epub)
    doc = EpubParser().parse(epub)

    toc_links = [
        b
        for b in doc.chapters[0].blocks
        if isinstance(b, ParagraphBlock) and b.rich and "bbref:" in b.rich
    ]
    assert len(toc_links) == 2
    for para in toc_links:
        assert "ch-body.xhtml" not in (para.rich or "")
        assert "#" not in (para.rich or "").replace("bbref:", "")
        assert para.text == strip_tags(para.rich or "")

    body = doc.chapters[1]
    heading = next(b for b in body.blocks if isinstance(b, HeadingBlock))
    assert heading.anchor_id is not None
    assert heading.anchor_id.startswith("a")

    footnote = next(
        b for b in body.blocks if isinstance(b, ParagraphBlock) and "Footnote" in b.text
    )
    assert footnote.anchor_id is not None

    marker = next(b for b in body.blocks if isinstance(b, ParagraphBlock) and "See note" in b.text)
    assert marker.rich is not None
    assert "bbref:" in marker.rich
    assert "#fn1" not in marker.rich


def test_translate_survive_render_epub(tmp_path: Path) -> None:
    """Full-length path: targets survive → working cross-chapter + footnote hrefs."""
    epub = tmp_path / "refs.epub"
    _mini_epub_with_toc_and_footnote(epub)
    doc = EpubParser().parse(epub)
    out = EpubRenderer().render(doc, tmp_path / "out", stem="refs")

    with zipfile.ZipFile(out) as zf:
        toc_xhtml = zf.read("OEBPS/chap-1.xhtml").decode("utf-8")
        body_xhtml = zf.read("OEBPS/chap-2.xhtml").decode("utf-8")

    assert "bbref:" not in toc_xhtml
    assert "bbref:" not in body_xhtml
    assert 'href="chap-2.xhtml#' in toc_xhtml
    assert "<strong>About this edition</strong>" in toc_xhtml
    # Footnote marker keeps <a> around <sup>; target has matching id.
    assert "<sup>1</sup>" in body_xhtml
    assert 'href="#' in body_xhtml or "href='#" in body_xhtml
    fn = next(
        b
        for b in doc.chapters[1].blocks
        if isinstance(b, ParagraphBlock) and b.anchor_id and "Footnote" in b.text
    )
    assert f'id="{fn.anchor_id}"' in body_xhtml
    sec = next(b for b in doc.chapters[1].blocks if isinstance(b, HeadingBlock))
    assert f'id="{sec.anchor_id}"' in body_xhtml


def test_condense_missing_target_unwraps(tmp_path: Path) -> None:
    from bs4 import BeautifulSoup

    epub = tmp_path / "refs.epub"
    _mini_epub_with_toc_and_footnote(epub)
    doc = EpubParser().parse(epub)
    body = doc.chapters[1]
    # Drop the footnote paragraph (condense removed the target).
    body.blocks = [
        b
        for b in body.blocks
        if not (isinstance(b, ParagraphBlock) and b.text.startswith("Footnote"))
    ]
    out = EpubRenderer().render(doc, tmp_path / "out2", stem="missing")
    with zipfile.ZipFile(out) as zf:
        body_xhtml = zf.read("OEBPS/chap-2.xhtml").decode("utf-8")

    assert "<sup>1</sup>" in body_xhtml
    assert "bbref:" not in body_xhtml
    soup = BeautifulSoup(body_xhtml, "html.parser")
    # Marker must not keep a dead <a> around the superscript.
    assert not any(a.find("sup") for a in soup.find_all("a"))


def test_markdown_unwraps_bbref(tmp_path: Path) -> None:
    doc = Document(
        metadata=DocumentMetadata(title="T", source_format="epub"),
        chapters=[
            Chapter(
                title="C",
                blocks=[
                    ParagraphBlock(
                        text="About this edition",
                        rich='<a href="bbref:a1"><strong>About this edition</strong></a>',
                    )
                ],
            )
        ],
    )
    md = MarkdownRenderer().render(doc, tmp_path).read_text(encoding="utf-8")
    assert "bbref:" not in md
    assert "](#" not in md
    assert "**About this edition**" in md


def test_external_http_still_round_trips() -> None:
    rich = sanitize_inline('<a href="https://example.com/x">link</a>')
    assert rich == '<a href="https://example.com/x">link</a>'
    html = block_to_html(
        ParagraphBlock(text="link", rich=rich),
        lambda _i: None,
        ref_resolve=lambda _a: None,
    )
    assert 'href="https://example.com/x"' in html
