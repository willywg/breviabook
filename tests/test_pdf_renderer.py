"""Phase 7: PDF renderer — HTML builder (always) + real PDF (skipped if libs absent)."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from brevia.ir.models import (
    Chapter,
    Document,
    DocumentMetadata,
    ImageAsset,
    ImageBlock,
    ParagraphBlock,
    TableBlock,
)
from brevia.parsers.epub_parser import EpubParser
from brevia.render.pdf_renderer import PdfRenderer, build_html, weasyprint_available

FIXTURE = Path(__file__).parent / "fixtures" / "sample.epub"


def test_importing_module_never_requires_system_libs() -> None:
    # Importing the renderer must succeed even when weasyprint's libs are missing.
    importlib.import_module("brevia.render.pdf_renderer")


def test_build_html_contains_all_elements() -> None:
    doc = EpubParser().parse(FIXTURE)
    html = build_html(doc)
    assert "Brevia Sample Book" in html
    assert '<section class="chapter">' in html
    assert "<pre><code" in html
    assert "def hello() -&gt; str:" in html  # code escaped + verbatim
    assert "data:image/png;base64," in html  # image inlined as data URI
    assert "<table>" in html
    assert html.startswith("<!DOCTYPE html>")


def test_build_html_is_deterministic() -> None:
    doc = EpubParser().parse(FIXTURE)
    assert build_html(doc) == build_html(doc)


def test_build_html_no_chapter_title_duplication() -> None:
    doc = EpubParser().parse(FIXTURE)
    html = build_html(doc)
    # Chapter title comes from the body <h1>; we must not also inject an <h2>.
    assert "<h2>Chapter One</h2>" not in html


def test_build_html_injects_title_when_no_leading_heading() -> None:
    doc = Document(
        metadata=DocumentMetadata(title="Book", source_format="epub"),
        chapters=[Chapter(title="Intro", blocks=[ParagraphBlock(text="body")])],
    )
    assert "<h2>Intro</h2>" in build_html(doc)


def test_image_only_kept_assets_inlined() -> None:
    doc = Document(
        metadata=DocumentMetadata(title="T", source_format="epub"),
        images={"k": ImageAsset(image_id="k", data=b"\x89PNGdata", mime="image/png")},
        chapters=[
            Chapter(blocks=[ImageBlock(image_id="k", caption="c"), TableBlock(rows=[["a"]])])
        ],
    )
    html = build_html(doc)
    assert "data:image/png;base64," in html
    assert "<figcaption>c</figcaption>" in html


@pytest.mark.skipif(not weasyprint_available(), reason="weasyprint system libs not installed")
def test_renders_real_pdf(tmp_path: Path) -> None:
    doc = EpubParser().parse(FIXTURE)
    out = PdfRenderer().render(doc, tmp_path)
    assert out.exists()
    assert out.read_bytes().startswith(b"%PDF")
    assert out.stat().st_size > 500
