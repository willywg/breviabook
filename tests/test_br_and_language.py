"""F3 (<br> in rich) + F5 (translated dc:language) — feat--br-and-language."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from breviabook.ir.models import Chapter, Document, DocumentMetadata, ParagraphBlock
from breviabook.llm.base import Message
from breviabook.llm.usage import Usage
from breviabook.parsers.epub_parser import EpubParser
from breviabook.render.epub_renderer import EpubRenderer
from breviabook.render.html import block_to_html
from breviabook.render.md_renderer import MarkdownRenderer
from breviabook.translate.translator import Translator
from breviabook.utils import htmlsan
from breviabook.utils.htmlsan import sanitize_inline, strip_tags
from breviabook.utils.langcodes import to_bcp47


class ScriptedProvider:
    name = "scripted"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.usage = Usage()

    async def generate(self, messages: list[Message], model: str, **opts: object) -> str:
        return self.reply


def test_safe_link_schemes_untouched() -> None:
    """F1 stay parked — this batch must not widen the href allowlist."""
    assert htmlsan._SAFE_LINK_SCHEMES == ("http://", "https://", "mailto:")


def test_sanitize_preserves_br_strip_tags_maps_to_space() -> None:
    rich = sanitize_inline("Editor<br/>Project<br>Editor")
    assert rich == "Editor<br/>Project<br/>Editor"
    assert strip_tags(rich) == "Editor Project Editor"


def test_html_and_md_emit_br(tmp_path: Path) -> None:
    block = ParagraphBlock(
        text="Editor Project",
        rich="Editor<br/>Project",
    )
    html = block_to_html(block, lambda _i: None)
    assert "<br/>" in html
    assert "Editor<br/>Project" in html

    doc = Document(
        metadata=DocumentMetadata(title="T", source_format="epub"),
        chapters=[Chapter(title="C", blocks=[block])],
    )
    md = MarkdownRenderer().render(doc, tmp_path).read_text(encoding="utf-8")
    assert "Editor  \nProject" in md


def test_parser_br_rich_text_invariant(tmp_path: Path) -> None:
    epub = tmp_path / "br.epub"
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
        '<item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
        '</manifest><spine><itemref idref="c1"/></spine></package>'
    )
    ch = (
        '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>'
        "<h1>Credits</h1>"
        "<p>Editor<br/>Project Editor<br/>Production Editor</p>"
        "</body></html>"
    )
    with zipfile.ZipFile(epub, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/c1.xhtml", ch)

    doc = EpubParser().parse(epub)
    para = next(b for _, b in doc.iter_blocks() if isinstance(b, ParagraphBlock))
    assert para.rich is not None
    assert para.rich.count("<br/>") == 2
    assert para.text == "Editor Project Editor Production Editor"
    assert para.text == strip_tags(para.rich)


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("Spanish", "es"),
        ("español", "es"),
        ("English", "en"),
        ("es", "es"),
        ("es-MX", "es-MX"),
        ("fr-ca", "fr-ca"),
        ("", "und"),
        ("Klingon", "und"),
    ],
)
def test_to_bcp47(name: str, code: str) -> None:
    assert to_bcp47(name) == code


async def test_translate_sets_metadata_language_and_opf(tmp_path: Path) -> None:
    doc = Document(
        metadata=DocumentMetadata(title="Think", language="en", source_format="epub"),
        chapters=[Chapter(title="One", blocks=[ParagraphBlock(text="Hello")])],
    )
    reply = json.dumps({"translations": {"1": "Uno", "2": "Hola"}})
    out = await Translator(ScriptedProvider(reply), "m", "Spanish").translate_document(doc)
    assert out.metadata.language == "es"
    assert out.metadata.title == "Think"

    epub_path = EpubRenderer().render(out, tmp_path, stem="es-book")
    with zipfile.ZipFile(epub_path) as zf:
        opf = zf.read("OEBPS/content.opf").decode("utf-8")
    assert "<dc:language>es</dc:language>" in opf
