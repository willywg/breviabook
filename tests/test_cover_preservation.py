"""F2 — OPF cover meta → IR cover_image_id → EPUB cover-image + cover.xhtml."""

from __future__ import annotations

import binascii
import struct
import zipfile
import zlib
from pathlib import Path
from xml.etree import ElementTree as ET

from breviabook.images.selector import ImageSelector
from breviabook.ir.models import (
    Chapter,
    Document,
    DocumentMetadata,
    ImageAsset,
    ImageBlock,
    ParagraphBlock,
)
from breviabook.parsers.epub_parser import EpubParser
from breviabook.render.epub_renderer import EpubRenderer


def _png() -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", binascii.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff")
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _epub_with_cover_meta_only(path: Path, cover_bytes: bytes) -> None:
    """Cover declared in OPF meta only — image never appears in a spine XHTML."""
    container = (
        '<?xml version="1.0"?><container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
        '<rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="b"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="b">x</dc:identifier><dc:title>Covered Book</dc:title>'
        '<dc:language>en</dc:language><meta name="cover" content="cover"/>'
        "</metadata><manifest>"
        '<item id="cover" href="images/cover.png" media-type="image/png"/>'
        '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
        '</manifest><spine><itemref idref="c1"/></spine></package>'
    )
    ch = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>'
        "<h1>Chapter</h1><p>Body text.</p></body></html>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/c1.xhtml", ch)
        zf.writestr("OEBPS/images/cover.png", cover_bytes)


def test_parser_loads_cover_from_opf_meta(tmp_path: Path) -> None:
    cover = _png()
    epub = tmp_path / "cover.epub"
    _epub_with_cover_meta_only(epub, cover)
    doc = EpubParser().parse(epub)
    assert doc.metadata.cover_image_id == "cover"
    assert "cover" in doc.images
    assert doc.images["cover"].data == cover
    # Body chapter has no ImageBlock — cover came solely from OPF.
    assert not any(isinstance(b, ImageBlock) for _, b in doc.iter_blocks())


def test_selector_keeps_cover_orphan() -> None:
    doc = Document(
        metadata=DocumentMetadata(title="T", source_format="epub", cover_image_id="cover"),
        images={
            "cover": ImageAsset(image_id="cover", data=b"COVER", mime="image/png"),
            "orphan": ImageAsset(image_id="orphan", data=b"ORPH", mime="image/png"),
        },
        chapters=[Chapter(title="C", blocks=[ParagraphBlock(text="hi")])],
    )
    result = ImageSelector().select(doc)
    assert "cover" in result.document.images
    assert "orphan" not in result.document.images
    assert result.kept_image_ids == ["cover"]


def test_renderer_emits_cover_opf_and_xhtml(tmp_path: Path) -> None:
    cover = _png()
    doc = Document(
        metadata=DocumentMetadata(
            title="Covered", source_format="epub", language="en", cover_image_id="cover"
        ),
        images={"cover": ImageAsset(image_id="cover", data=cover, mime="image/png")},
        chapters=[Chapter(title="One", blocks=[ParagraphBlock(text="Hello")])],
    )
    out = EpubRenderer().render(doc, tmp_path, stem="with-cover")
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "OEBPS/cover.xhtml" in names
        opf = zf.read("OEBPS/content.opf").decode("utf-8")
        cover_xhtml = zf.read("OEBPS/cover.xhtml").decode("utf-8")

    assert 'properties="cover-image"' in opf
    assert '<meta name="cover" content="' in opf
    assert "<img src=" in cover_xhtml

    root = ET.fromstring(opf)
    ns = {"opf": "http://www.idpf.org/2007/opf"}
    spine_refs = [el.get("idref") for el in root.findall("opf:spine/opf:itemref", ns)]
    assert spine_refs[0] == "cover-page"


def test_renderer_dedupes_leading_cover_chapter(tmp_path: Path) -> None:
    cover = _png()
    doc = Document(
        metadata=DocumentMetadata(title="T", source_format="epub", cover_image_id="cover"),
        images={"cover": ImageAsset(image_id="cover", data=cover, mime="image/png")},
        chapters=[
            Chapter(title="Cover", blocks=[ImageBlock(image_id="cover")]),
            Chapter(title="One", blocks=[ParagraphBlock(text="Hello")]),
        ],
    )
    out = EpubRenderer().render(doc, tmp_path)
    with zipfile.ZipFile(out) as zf:
        names = [n for n in zf.namelist() if n.endswith(".xhtml") and "nav" not in n]
    # cover.xhtml + one body chapter (leading ImageBlock chapter dropped)
    assert "OEBPS/cover.xhtml" in names
    assert "OEBPS/chap-1.xhtml" in names
    assert "OEBPS/chap-2.xhtml" not in names


def test_cover_round_trip(tmp_path: Path) -> None:
    cover = _png()
    src = tmp_path / "src.epub"
    _epub_with_cover_meta_only(src, cover)
    original = EpubParser().parse(src)
    cleaned = ImageSelector().select(original).document
    out = EpubRenderer().render(cleaned, tmp_path / "out")
    reparsed = EpubParser().parse(out)
    assert reparsed.metadata.cover_image_id is not None
    assert cover in {a.data for a in reparsed.images.values()}
    with zipfile.ZipFile(out) as zf:
        opf = zf.read("OEBPS/content.opf").decode("utf-8")
    assert 'properties="cover-image"' in opf
