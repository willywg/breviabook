"""Phase 8: PDF → IR parsing (heuristic blocks, outline chapters, deduped images)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from breviabook.ir.models import CodeBlock, ImageBlock, TableBlock
from breviabook.parsers.base import ParseError
from breviabook.parsers.pdf_parser import PdfParser, TocEntry, load_manual_toc

FIXTURE = Path(__file__).parent / "fixtures" / "sample.pdf"


@pytest.fixture
def doc():
    return PdfParser().parse(FIXTURE)


def test_metadata_and_chapters_from_outline(doc) -> None:
    assert doc.metadata.source_format == "pdf"
    assert doc.metadata.title == "BreviaBook Sample PDF"
    assert [c.title for c in doc.chapters] == ["Chapter One", "Chapter Two"]


def test_paragraphs_merged_from_wrapped_lines(doc) -> None:
    paras = [b.text for _, b in doc.iter_blocks() if b.type == "paragraph"]
    # The first paragraph was wrapped across two PDF lines; it must come back as one.
    assert (
        "This is the first paragraph of the sample PDF, with enough words to form a block." in paras
    )


def test_code_detected_via_monospace_font(doc) -> None:
    code = next(b for _, b in doc.iter_blocks() if isinstance(b, CodeBlock))
    assert "def hello()" in code.text
    assert 'return "world"' in code.text


def test_table_extracted_and_not_duplicated_as_text(doc) -> None:
    tables = [b for _, b in doc.iter_blocks() if isinstance(b, TableBlock)]
    assert len(tables) == 1
    assert tables[0].rows == [["Name", "Value"], ["alpha", "1"]]
    # The cell text must not also appear as a paragraph.
    paras = [b.text for _, b in doc.iter_blocks() if b.type == "paragraph"]
    assert "alpha 1" not in paras


def test_image_deduplicated_to_single_asset(doc) -> None:
    # The shared XObject is reported on both pages; we embed it exactly once.
    assert len(doc.images) == 1
    refs = [b for _, b in doc.iter_blocks() if isinstance(b, ImageBlock)]
    assert len(refs) == 1
    asset = next(iter(doc.images.values()))
    assert asset.mime == "image/png"
    assert asset.data.startswith(b"\x89PNG")


def test_build_without_toc_is_single_chapter() -> None:
    parser = PdfParser()
    extracted = parser.extract(FIXTURE)
    single = parser.build(extracted, None)
    assert len(single.chapters) == 1
    assert single.chapters[0].title == "BreviaBook Sample PDF"


def test_manual_toc_overrides_outline() -> None:
    parser = PdfParser()
    extracted = parser.extract(FIXTURE)
    doc = parser.build(extracted, [TocEntry(title="Everything", start_page=0)])
    assert [c.title for c in doc.chapters] == ["Everything"]


def test_load_manual_toc(tmp_path: Path) -> None:
    path = tmp_path / "toc.json"
    path.write_text(
        json.dumps([{"title": "A", "start_page": 0}, {"title": "B", "start_page": 3}]),
        encoding="utf-8",
    )
    entries = load_manual_toc(path)
    assert entries == [TocEntry("A", 0), TocEntry("B", 3)]


def test_load_manual_toc_rejects_bad_shape(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_manual_toc(path)


def test_non_pdf_raises_parse_error(tmp_path: Path) -> None:
    fake = tmp_path / "x.pdf"
    fake.write_bytes(b"not a pdf at all")
    with pytest.raises(ParseError):
        PdfParser().parse(fake)
