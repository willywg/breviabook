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


# Text-bearing blocks carry an optional ``rich`` field: sanitized inline HTML (emphasis, links,
# color) produced by the parser. ``text`` is always the plain-text projection and drives token
# budgeting/condensation unchanged; renderers and the translator prefer ``rich`` when present.
# Invariant when set: ``text == htmlsan.strip_tags(rich)``.

# Block presentation (Phase A fidelity): optional shell fields. ``None`` = UA default.
# Distinct from ``rich`` (inline markup). Condensation must copy these from the source block.
Align = Literal["left", "center", "right"]
MarkerType = Literal["disc", "circle", "square", "none"]


class HeadingBlock(BaseModel):
    type: Literal["heading"] = "heading"
    level: int = Field(ge=1, le=6)
    text: str
    rich: str | None = None
    align: Align | None = None


class ParagraphBlock(BaseModel):
    type: Literal["paragraph"] = "paragraph"
    text: str
    rich: str | None = None
    align: Align | None = None


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
    rich: str | None = None
    align: Align | None = None


class ListBlock(BaseModel):
    type: Literal["list"] = "list"
    items: list[str]
    ordered: bool = False
    # Per-item sanitized inline HTML, aligned 1:1 with ``items``; None when no item has markup.
    items_rich: list[str] | None = None
    marker_type: MarkerType | None = None
    # Safe CSS color for the bullet/number; renderers emit ``li::marker`` only (never ``ul`` color).
    marker_color: str | None = None


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
