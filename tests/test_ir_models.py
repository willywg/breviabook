"""IR model tests: discriminated-union round-trip and helpers."""

from __future__ import annotations

from breviabook.ir.models import (
    Chapter,
    CodeBlock,
    Document,
    DocumentMetadata,
    HeadingBlock,
    ImageAsset,
    ImageBlock,
)


def _doc() -> Document:
    return Document(
        metadata=DocumentMetadata(title="T", source_format="epub"),
        images={"img1": ImageAsset(image_id="img1", data=b"\x89PNG", mime="image/png")},
        chapters=[
            Chapter(
                title="One",
                blocks=[
                    HeadingBlock(level=1, text="One"),
                    CodeBlock(language="python", text="x = 1\n"),
                    ImageBlock(image_id="img1", caption="fig"),
                ],
            )
        ],
    )


def test_discriminated_union_round_trip() -> None:
    doc = _doc()
    restored = Document.model_validate(doc.model_dump())
    assert isinstance(restored.chapters[0].blocks[0], HeadingBlock)
    assert isinstance(restored.chapters[0].blocks[1], CodeBlock)
    assert isinstance(restored.chapters[0].blocks[2], ImageBlock)
    # Code text is preserved verbatim through serialization.
    assert restored.chapters[0].blocks[1].text == "x = 1\n"


def test_iter_blocks_reading_order() -> None:
    doc = _doc()
    kinds = [b.type for _, b in doc.iter_blocks()]
    assert kinds == ["heading", "code", "image"]
