"""The Intermediate Representation (IR) — BreviaBook's format-agnostic document model.

This is the architectural keystone (ROADMAP §3). Parsers produce a ``Document``; the
condenser/translator transform its text blocks; renderers consume it. ``code`` and
``image`` blocks are structural and are never summarized or split.

Blocks form a discriminated union keyed on ``type`` so the model (de)serializes cleanly
for Phase 3 checkpoints.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated, Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Blocks
# --------------------------------------------------------------------------- #


class HeadingBlock(BaseModel):
    type: Literal["heading"] = "heading"
    level: int = Field(ge=1, le=6)
    text: str


class ParagraphBlock(BaseModel):
    type: Literal["paragraph"] = "paragraph"
    text: str


class CodeBlock(BaseModel):
    """A verbatim code listing. NEVER summarized, reflowed, or split (ROADMAP §7.2)."""

    type: Literal["code"] = "code"
    language: str | None = None
    text: str


class ImageBlock(BaseModel):
    """References an :class:`ImageAsset` by id; the bytes live in ``Document.images``."""

    type: Literal["image"] = "image"
    image_id: str
    caption: str | None = None


class TableBlock(BaseModel):
    type: Literal["table"] = "table"
    rows: list[list[str]]


class QuoteBlock(BaseModel):
    type: Literal["quote"] = "quote"
    text: str


class ListBlock(BaseModel):
    type: Literal["list"] = "list"
    items: list[str]
    ordered: bool = False


Block = Annotated[
    HeadingBlock | ParagraphBlock | CodeBlock | ImageBlock | TableBlock | QuoteBlock | ListBlock,
    Field(discriminator="type"),
]
"""Discriminated union of every block kind (ROADMAP §3)."""


# --------------------------------------------------------------------------- #
# Assets, chapters, document
# --------------------------------------------------------------------------- #


class ImageAsset(BaseModel):
    """A binary image extracted from the source, referenced by ``image_id``."""

    image_id: str
    data: bytes
    mime: str
    original_path: str | None = None
    alt_text: str | None = None


class DocumentMetadata(BaseModel):
    title: str
    author: str | None = None
    language: str | None = None
    source_format: str  # "epub" | "pdf"


class Chapter(BaseModel):
    title: str | None = None
    blocks: list[Block] = Field(default_factory=list)


class Document(BaseModel):
    """A whole book in the IR: metadata, image assets by id, and ordered chapters."""

    metadata: DocumentMetadata
    images: dict[str, ImageAsset] = Field(default_factory=dict)
    chapters: list[Chapter] = Field(default_factory=list)

    def iter_blocks(self) -> Iterator[tuple[Chapter, Block]]:
        """Yield ``(chapter, block)`` for every block in reading order."""
        for chapter in self.chapters:
            for block in chapter.blocks:
                yield chapter, block
