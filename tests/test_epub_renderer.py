"""Phase 6: EPUB renderer + IR round-trip (render -> parse equivalence, ROADMAP §11)."""

from __future__ import annotations

import zipfile
from pathlib import Path

from breviabook.images.selector import ImageSelector
from breviabook.ir.models import (
    Chapter,
    CodeBlock,
    Document,
    DocumentMetadata,
    ImageAsset,
    ImageBlock,
)
from breviabook.parsers.epub_parser import EpubParser
from breviabook.render.epub_renderer import EpubRenderer

FIXTURE = Path(__file__).parent / "fixtures" / "sample.epub"


def test_renders_valid_zip_with_mimetype_first(tmp_path: Path) -> None:
    doc = EpubParser().parse(FIXTURE)
    out = EpubRenderer().render(doc, tmp_path)
    assert zipfile.is_zipfile(out)
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert names[0] == "mimetype"  # must be first
        info = zf.getinfo("mimetype")
        assert info.compress_type == zipfile.ZIP_STORED  # stored, not deflated
        assert zf.read("mimetype") == b"application/epub+zip"
        assert "META-INF/container.xml" in names
        assert "OEBPS/content.opf" in names
        assert "OEBPS/nav.xhtml" in names


def test_round_trip_preserves_structure(tmp_path: Path) -> None:
    original = EpubParser().parse(FIXTURE)
    out = EpubRenderer().render(original, tmp_path)
    reparsed = EpubParser().parse(out)

    assert reparsed.metadata.title == original.metadata.title
    assert [c.title for c in reparsed.chapters] == [c.title for c in original.chapters]
    for oc, rc in zip(original.chapters, reparsed.chapters, strict=True):
        assert [b.type for b in oc.blocks] == [b.type for b in rc.blocks]


def test_round_trip_preserves_code_verbatim(tmp_path: Path) -> None:
    original = EpubParser().parse(FIXTURE)
    out = EpubRenderer().render(original, tmp_path)
    reparsed = EpubParser().parse(out)

    def first_code(doc: Document) -> CodeBlock:
        return next(b for _, b in doc.iter_blocks() if isinstance(b, CodeBlock))

    assert first_code(reparsed).text == first_code(original).text
    assert first_code(reparsed).language == first_code(original).language


def test_round_trip_preserves_image_bytes(tmp_path: Path) -> None:
    original = EpubParser().parse(FIXTURE)
    out = EpubRenderer().render(original, tmp_path)
    reparsed = EpubParser().parse(out)

    assert len(reparsed.images) == len(original.images) == 1
    orig_bytes = next(iter(original.images.values())).data
    new_bytes = next(iter(reparsed.images.values())).data
    assert new_bytes == orig_bytes


def test_embeds_only_kept_images(tmp_path: Path) -> None:
    # A doc with an orphan asset: selector prunes it, renderer embeds only the kept one.
    doc = Document(
        metadata=DocumentMetadata(title="T", source_format="epub"),
        images={
            "keep1": ImageAsset(image_id="keep1", data=b"\x89PNGkeep", mime="image/png"),
            "orphan": ImageAsset(image_id="orphan", data=b"\x89PNGorph", mime="image/png"),
        },
        chapters=[Chapter(title="C", blocks=[ImageBlock(image_id="keep1", caption="cap")])],
    )
    cleaned = ImageSelector().select(doc).document
    out = EpubRenderer().render(cleaned, tmp_path)
    with zipfile.ZipFile(out) as zf:
        image_files = [n for n in zf.namelist() if n.startswith("OEBPS/images/")]
        assert len(image_files) == 1
        assert zf.read(image_files[0]) == b"\x89PNGkeep"


def test_deterministic_output(tmp_path: Path) -> None:
    doc = EpubParser().parse(FIXTURE)
    a = EpubRenderer().render(doc, tmp_path / "a")
    b = EpubRenderer().render(doc, tmp_path / "b")
    assert a.read_bytes() == b.read_bytes()


def test_rich_inline_html_emitted_in_chapter(tmp_path: Path) -> None:
    from breviabook.ir.models import HeadingBlock, ParagraphBlock

    doc = Document(
        metadata=DocumentMetadata(title="T", source_format="epub"),
        chapters=[
            Chapter(
                blocks=[
                    HeadingBlock(
                        level=2,
                        text="CHAPTER 1 Guiding",
                        rich='<span style="color:#9e0b0f"><strong>Guiding</strong></span>',
                    ),
                    ParagraphBlock(text="plain", rich=None),
                ]
            )
        ],
    )
    out = EpubRenderer().render(doc, tmp_path, stem="styled")
    with zipfile.ZipFile(out) as zf:
        names = [n for n in zf.namelist() if n.endswith(".xhtml") and "nav" not in n]
        body = zf.read(f"OEBPS/{names[0].split('/')[-1]}").decode("utf-8")
    assert '<span style="color:#9e0b0f"><strong>Guiding</strong></span>' in body
    assert "<p>plain</p>" in body  # plain block still escaped-plain
