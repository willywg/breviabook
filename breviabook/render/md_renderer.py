"""Markdown renderer: ``Document`` (IR) -> a ``.md`` file plus an ``images/`` folder.

The simplest renderer; it validates the IR end-to-end without an LLM (ROADMAP §10 Phase 2).
Image assets are written to disk and referenced with relative links.
"""

from __future__ import annotations

from pathlib import Path
from typing import assert_never

from breviabook.ir.models import (
    Block,
    CodeBlock,
    Document,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    ParagraphBlock,
    QuoteBlock,
    TableBlock,
)
from breviabook.render.base import image_filename

_IMAGES_DIR = "images"


class MarkdownRenderer:
    """Renders the IR to GitHub-flavored Markdown."""

    name = "md"

    def render(self, doc: Document, out_dir: Path, *, stem: str = "condensed-book") -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        image_links = self._write_images(doc, out_dir)

        parts: list[str] = []
        if doc.metadata.title:
            parts.append(f"# {doc.metadata.title}")
        for chapter in doc.chapters:
            # Emit the chapter title only when the body doesn't already open with a heading
            # (the parser often derives chapter.title from that very heading — avoid dupes).
            first_is_heading = bool(chapter.blocks) and isinstance(chapter.blocks[0], HeadingBlock)
            if chapter.title and not first_is_heading:
                parts.append(f"## {chapter.title}")
            for block in chapter.blocks:
                parts.append(self._render_block(block, image_links))

        out_file = out_dir / f"{stem}.md"
        out_file.write_text("\n\n".join(parts) + "\n", encoding="utf-8")
        return out_file

    def _write_images(self, doc: Document, out_dir: Path) -> dict[str, str]:
        """Write each asset under ``images/`` and return ``{image_id: relative_link}``."""
        links: dict[str, str] = {}
        if not doc.images:
            return links
        images_dir = out_dir / _IMAGES_DIR
        images_dir.mkdir(parents=True, exist_ok=True)
        for image_id, asset in doc.images.items():
            filename = image_filename(asset)
            (images_dir / filename).write_bytes(asset.data)
            links[image_id] = f"{_IMAGES_DIR}/{filename}"
        return links

    def _render_block(self, block: Block, image_links: dict[str, str]) -> str:
        if isinstance(block, HeadingBlock):
            return f"{'#' * block.level} {block.text}"
        if isinstance(block, ParagraphBlock):
            return block.text
        if isinstance(block, CodeBlock):
            return f"```{block.language or ''}\n{block.text.rstrip(chr(10))}\n```"
        if isinstance(block, QuoteBlock):
            return "\n".join(f"> {line}" for line in block.text.splitlines() or [""])
        if isinstance(block, ListBlock):
            lines = [
                f"{f'{i}.' if block.ordered else '-'} {item}"
                for i, item in enumerate(block.items, 1)
            ]
            return "\n".join(lines)
        if isinstance(block, TableBlock):
            return self._render_table(block)
        if isinstance(block, ImageBlock):
            link = image_links.get(block.image_id, block.image_id)
            return f"![{block.caption or ''}]({link})"
        assert_never(block)

    def _render_table(self, table: TableBlock) -> str:
        if not table.rows:
            return ""

        def esc(cell: str) -> str:
            return cell.replace("|", r"\|").replace("\n", " ")

        header, *body = table.rows
        lines = ["| " + " | ".join(esc(c) for c in header) + " |"]
        lines.append("| " + " | ".join("---" for _ in header) + " |")
        for row in body:
            lines.append("| " + " | ".join(esc(c) for c in row) + " |")
        return "\n".join(lines)
