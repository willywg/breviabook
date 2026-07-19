"""F4 (block class bold/italic) + F7 (image align) — Round 2 fidelity."""

from __future__ import annotations

import zipfile
from pathlib import Path

from bs4 import BeautifulSoup

from breviabook.ir.models import (
    Chapter,
    Document,
    DocumentMetadata,
    ImageAsset,
    ImageBlock,
    ParagraphBlock,
)
from breviabook.parsers.epub_parser import EpubParser
from breviabook.render.html import block_to_html
from breviabook.render.md_renderer import MarkdownRenderer
from breviabook.utils.htmlsan import (
    _SAFE_LINK_SCHEMES,
    parse_class_styles,
    sanitize_inline,
    strip_tags,
)

# Tiny 1×1 PNG
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _noop_src(_image_id: str) -> str | None:
    return "img.png"


def test_safe_link_schemes_untouched() -> None:
    assert _SAFE_LINK_SCHEMES == ("http://", "https://", "mailto:")


def test_class_styles_capture_font_weight_and_style() -> None:
    cs = parse_class_styles(".legalnotice { font-weight: bold } .subtitle { font-style: italic }")
    assert cs["legalnotice"].get("bold") == "1"
    assert cs["subtitle"].get("italic") == "1"


def test_block_class_bold_applies_on_root_tag() -> None:
    """Parser-style call: sanitize the <p> Tag itself (not a string fragment)."""
    cs = parse_class_styles(".legalnotice { font-weight: bold }")
    p = BeautifulSoup('<p class="legalnotice">Notice of Rights</p>', "html.parser").p
    assert p is not None
    rich = sanitize_inline(p, cs)
    assert rich == "<strong>Notice of Rights</strong>"
    assert strip_tags(rich) == "Notice of Rights"


def test_block_class_italic_and_color_coexist() -> None:
    cs = parse_class_styles(".copy { font-style: italic; color: #9e0b0f }")
    p = BeautifulSoup('<p class="copy">Subtitle line</p>', "html.parser").p
    assert p is not None
    rich = sanitize_inline(p, cs)
    assert "<em>Subtitle line</em>" in rich
    assert 'style="color:#9e0b0f"' in rich
    assert strip_tags(rich) == "Subtitle line"


def test_block_class_bold_and_color_coexist() -> None:
    cs = parse_class_styles(".legalnotice { font-weight: bold; color: #333 }")
    p = BeautifulSoup('<p class="legalnotice">Notice of Liability</p>', "html.parser").p
    assert p is not None
    rich = sanitize_inline(p, cs)
    assert "<strong>Notice of Liability</strong>" in rich
    assert 'style="color:#333"' in rich


def test_epub_parser_legalnotice_rich_is_strong(tmp_path: Path) -> None:
    path = tmp_path / "legal.epub"
    _mini_epub_legalnotice(path)
    doc = EpubParser().parse(path)
    notices = [
        b
        for _, b in doc.iter_blocks()
        if isinstance(b, ParagraphBlock) and "Notice of Rights" in b.text
    ]
    assert len(notices) == 1
    assert notices[0].rich is not None
    assert "<strong>" in notices[0].rich
    assert notices[0].text == "Notice of Rights"


def _mini_epub_legalnotice(path: Path) -> None:
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
    css = ".legalnotice { font-weight: bold }"
    ch = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head>'
        '<title>C</title><link rel="stylesheet" href="s.css"/></head><body>'
        '<p class="legalnotice">Notice of Rights</p>'
        "<p>Body text.</p></body></html>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/s.css", css)
        zf.writestr("OEBPS/c1.xhtml", ch)


def _mini_epub_centered_figure(path: Path) -> None:
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
        '<item id="img1" href="fig.png" media-type="image/png"/>'
        '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
        '</manifest><spine><itemref idref="c1"/></spine></package>'
    )
    css = ".center { text-align: center }"
    ch = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head>'
        '<title>C</title><link rel="stylesheet" href="s.css"/></head><body>'
        '<div class="center"><figure><img src="fig.png" alt="diagram"/>'
        "<figcaption>Figure 1</figcaption></figure></div>"
        "<p>After.</p></body></html>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/s.css", css)
        zf.writestr("OEBPS/c1.xhtml", ch)
        zf.writestr("OEBPS/fig.png", _PNG)


def test_centered_figure_sets_image_align(tmp_path: Path) -> None:
    path = tmp_path / "fig.epub"
    _mini_epub_centered_figure(path)
    doc = EpubParser().parse(path)
    images = [b for _, b in doc.iter_blocks() if isinstance(b, ImageBlock)]
    assert len(images) == 1
    assert images[0].align == "center"
    assert images[0].caption == "Figure 1"


def test_html_centers_figure_when_align_set() -> None:
    html = block_to_html(ImageBlock(image_id="i", caption="c", align="center"), _noop_src)
    assert 'style="text-align:center"' in html
    assert html.startswith("<figure ")
    assert "<img " in html


def test_markdown_discards_image_align(tmp_path: Path) -> None:
    doc = Document(
        metadata=DocumentMetadata(title="T", source_format="epub"),
        images={
            "i": ImageAsset(
                image_id="i",
                data=_PNG,
                mime="image/png",
                original_path="fig.png",
            )
        },
        chapters=[
            Chapter(
                title="C",
                blocks=[ImageBlock(image_id="i", caption="cap", align="center")],
            )
        ],
    )
    md = MarkdownRenderer().render(doc, tmp_path).read_text(encoding="utf-8")
    assert "text-align" not in md
    assert "bbref:" not in md
