"""Phase 2: parse EPUB -> render Markdown round-trip (no LLM)."""

from __future__ import annotations

from pathlib import Path

import pytest

from breviabook.ir.models import (
    Chapter,
    Document,
    DocumentMetadata,
    ImageAsset,
    ImageBlock,
    ListBlock,
    TableBlock,
)
from breviabook.parsers.epub_parser import EpubParser
from breviabook.render.md_renderer import MarkdownRenderer

FIXTURE = Path(__file__).parent / "fixtures" / "sample.epub"


@pytest.fixture
def rendered(tmp_path: Path) -> tuple[Path, str]:
    doc = EpubParser().parse(FIXTURE)
    out_file = MarkdownRenderer().render(doc, tmp_path)
    return out_file, out_file.read_text(encoding="utf-8")


def test_md_file_created(rendered: tuple[Path, str]) -> None:
    out_file, text = rendered
    assert out_file.name == "condensed-book.md"
    assert text.strip()


def test_headings_present(rendered: tuple[Path, str]) -> None:
    _, text = rendered
    assert "# Chapter One" in text
    assert "# Chapter Two" in text


def test_code_block_verbatim_and_fenced(rendered: tuple[Path, str]) -> None:
    _, text = rendered
    assert "```python" in text
    assert 'def hello() -> str:\n    return "world"' in text


def test_quote_and_list(rendered: tuple[Path, str]) -> None:
    _, text = rendered
    assert "> A quote worth keeping." in text
    assert "- First item" in text
    assert "- Second item" in text


def test_table_rendered(rendered: tuple[Path, str]) -> None:
    _, text = rendered
    assert "| Name | Value |" in text
    assert "| --- | --- |" in text
    assert "| alpha | 1 |" in text


def test_image_link_and_file_written(tmp_path: Path) -> None:
    doc = EpubParser().parse(FIXTURE)
    out_file = MarkdownRenderer().render(doc, tmp_path)
    text = out_file.read_text(encoding="utf-8")
    assert "![Figure 2.1 - the architecture](images/fig1.png)" in text
    img = tmp_path / "images" / "fig1.png"
    assert img.exists()
    assert img.read_bytes().startswith(b"\x89PNG")


def test_no_orphan_image_links(tmp_path: Path) -> None:
    doc = Document(
        metadata=DocumentMetadata(title="T", source_format="epub"),
        images={"x": ImageAsset(image_id="x", data=b"\x89PNG", mime="image/png")},
        chapters=[Chapter(blocks=[ImageBlock(image_id="x")])],
    )
    out_file = MarkdownRenderer().render(doc, tmp_path)
    assert (tmp_path / "images" / "x.png").exists()
    assert "(images/x.png)" in out_file.read_text(encoding="utf-8")


def test_chapter_title_not_duplicated(rendered: tuple[Path, str]) -> None:
    # The parser derives chapter.title from the leading <h1>; render it only once.
    _, text = rendered
    assert text.count("# Chapter One") == 1
    assert text.count("# Chapter Two") == 1


def test_synthetic_chapter_title_when_no_leading_heading(tmp_path: Path) -> None:
    from breviabook.ir.models import ParagraphBlock

    doc = Document(
        metadata=DocumentMetadata(title="Book", source_format="epub"),
        chapters=[Chapter(title="Intro", blocks=[ParagraphBlock(text="body")])],
    )
    text = MarkdownRenderer().render(doc, tmp_path).read_text(encoding="utf-8")
    assert "## Intro" in text


def test_ordered_list_and_table_escaping(tmp_path: Path) -> None:
    doc = Document(
        metadata=DocumentMetadata(title="T", source_format="epub"),
        chapters=[
            Chapter(
                blocks=[
                    ListBlock(items=["a", "b"], ordered=True),
                    TableBlock(rows=[["a|b", "c"], ["1", "2"]]),
                ]
            )
        ],
    )
    text = MarkdownRenderer().render(doc, tmp_path).read_text(encoding="utf-8")
    assert "1. a" in text and "2. b" in text
    assert r"a\|b" in text


def test_rich_converted_to_markdown_and_html_passthrough(tmp_path: Path) -> None:
    from breviabook.ir.models import HeadingBlock, ParagraphBlock
    from breviabook.render.md_renderer import MarkdownRenderer

    doc = Document(
        metadata=DocumentMetadata(title="T", source_format="epub"),
        chapters=[
            Chapter(
                blocks=[
                    HeadingBlock(
                        level=2,
                        text="CHAPTER 1 Don't think",
                        rich='<span style="color:#9e0b0f">CHAPTER 1</span> <strong>Don\'t</strong>',
                    ),
                    ParagraphBlock(
                        text="a link and really",
                        rich='<a href="https://x.com">a link</a> and <em>really</em>',
                    ),
                    ParagraphBlock(text="plain only"),
                ]
            )
        ],
    )
    out = MarkdownRenderer().render(doc, tmp_path, stem="m")
    md = out.read_text(encoding="utf-8")
    assert "**Don't**" in md  # strong -> markdown
    assert "*really*" in md  # em -> markdown
    assert "[a link](https://x.com)" in md  # link -> markdown
    assert '<span style="color:#9e0b0f">CHAPTER 1</span>' in md  # color span passes through
    assert "plain only" in md


def test_inline_image_rendered_in_md_and_html(tmp_path: Path) -> None:
    from breviabook.ir.models import HeadingBlock
    from breviabook.render.html import block_to_html
    from breviabook.render.md_renderer import MarkdownRenderer

    doc = Document(
        metadata=DocumentMetadata(title="T", source_format="epub"),
        images={"strike": ImageAsset(image_id="strike", data=b"\x89PNG", mime="image/png")},
        chapters=[
            Chapter(
                blocks=[
                    HeadingBlock(
                        level=2, text="Omit words", rich='Omit <img data-image-id="strike"/> words'
                    )
                ]
            )
        ],
    )
    out = MarkdownRenderer().render(doc, tmp_path, stem="m")
    md = out.read_text(encoding="utf-8")
    assert "![](images/" in md  # inline image resolved to a markdown image link

    html = block_to_html(
        doc.chapters[0].blocks[0], lambda i: "images/s.png" if i == "strike" else None
    )
    assert '<img src="images/s.png" alt=""/>' in html  # data-image-id resolved to real src
