"""Strategy A image selection (structural keep/drop)."""

from __future__ import annotations

from breviabook.images.selector import ImageSelector
from breviabook.ir.models import (
    Chapter,
    Document,
    DocumentMetadata,
    ImageAsset,
    ImageBlock,
    ParagraphBlock,
)


def _doc(images: dict[str, ImageAsset], chapters: list[Chapter]) -> Document:
    return Document(
        metadata=DocumentMetadata(title="T", source_format="epub"),
        images=images,
        chapters=chapters,
    )


def _asset(iid: str) -> ImageAsset:
    return ImageAsset(image_id=iid, data=b"\x89PNG", mime="image/png")


def test_keeps_referenced_drops_orphan_assets() -> None:
    doc = _doc(
        {"keep1": _asset("keep1"), "orphan": _asset("orphan")},
        [Chapter(blocks=[ParagraphBlock(text="x"), ImageBlock(image_id="keep1")])],
    )
    result = ImageSelector().select(doc)
    assert result.kept_image_ids == ["keep1"]
    assert result.dropped_image_ids == ["orphan"]
    assert set(result.document.images) == {"keep1"}


def test_strips_dangling_image_block() -> None:
    # ImageBlock references an id with no asset -> reference removed.
    doc = _doc(
        {},
        [Chapter(blocks=[ParagraphBlock(text="x"), ImageBlock(image_id="missing")])],
    )
    result = ImageSelector().select(doc)
    blocks = result.document.chapters[0].blocks
    assert all(b.type != "image" for b in blocks)
    assert result.document.images == {}


def test_no_images_is_noop() -> None:
    doc = _doc({}, [Chapter(blocks=[ParagraphBlock(text="x")])])
    result = ImageSelector().select(doc)
    assert result.kept_image_ids == []
    assert result.dropped_image_ids == []
    assert len(result.document.chapters[0].blocks) == 1


def test_inline_image_reference_keeps_asset(tmp_path) -> None:
    from breviabook.images.selector import ImageSelector
    from breviabook.ir.models import (
        Chapter,
        Document,
        DocumentMetadata,
        HeadingBlock,
        ImageAsset,
    )

    doc = Document(
        metadata=DocumentMetadata(title="T", source_format="epub"),
        images={"strike": ImageAsset(image_id="strike", data=b"x", mime="image/png")},
        chapters=[
            Chapter(
                blocks=[
                    HeadingBlock(
                        level=2,
                        text="Omit words",
                        rich='Omit <img data-image-id="strike"/> words',
                    )
                ]
            )
        ],
    )
    result = ImageSelector().select(doc)
    # The asset is referenced only inline (no ImageBlock) — it must NOT be dropped.
    assert "strike" in result.document.images
    assert result.dropped_image_ids == []
