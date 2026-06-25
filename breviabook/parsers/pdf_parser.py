"""PDF → IR parser (pdfplumber for text/tables, pypdf for images/metadata/outline).

We deliberately avoid PyMuPDF (AGPL, ROADMAP §14). PDFs carry no semantic structure, so blocks
are recovered heuristically from font and geometry:

- **code**: lines in a monospace font, grouped (line breaks preserved);
- **heading**: bold or noticeably larger than the body font;
- **paragraph**: remaining lines, merged across soft wraps, split on vertical gaps;
- **table**: pdfplumber tables, with their region excluded from the text so cells aren't
  duplicated as paragraphs;
- **image**: pypdf-extracted, deduped by content hash (a shared XObject is reported on several
  pages — we embed it once, at first occurrence; placement is best-effort).

Chapters come from the PDF outline, a manual TOC, an LLM-inferred TOC, or fall back to one
chapter. TOC resolution (incl. the async LLM path) happens outside this sync parser.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

import pdfplumber
from pypdf import PdfReader

from breviabook.ir.models import (
    Block,
    Chapter,
    CodeBlock,
    Document,
    DocumentMetadata,
    HeadingBlock,
    ImageAsset,
    ImageBlock,
    ParagraphBlock,
    TableBlock,
)
from breviabook.parsers.base import ParseError

_MONO_MARKERS = ("mono", "courier", "consol", "andale", "menlo")
_HEADING_FACTOR = 1.25
_H1_FACTOR = 1.6
_PARA_GAP_FACTOR = 0.6
_MIME_BY_EXT = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "tiff": "image/tiff",
}


@dataclass
class TocEntry:
    title: str
    start_page: int  # 0-based page index where the chapter begins


@dataclass
class ExtractedPdf:
    metadata: DocumentMetadata
    page_blocks: list[list[Block]]
    page_images: list[list[tuple[str, ImageAsset]]]  # per page: (content_hash, asset)
    outline: list[TocEntry]
    page_texts: list[str]


def load_manual_toc(path: Path) -> list[TocEntry]:
    """Load a manual TOC JSON file: ``[{"title": str, "start_page": int}, ...]``."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Manual TOC must be a JSON list of {title, start_page} objects")
    entries: list[TocEntry] = []
    for item in data:
        if not isinstance(item, dict) or "title" not in item or "start_page" not in item:
            raise ValueError("Each manual TOC entry needs 'title' and 'start_page'")
        entries.append(TocEntry(title=str(item["title"]), start_page=int(item["start_page"])))
    return entries


def _is_mono(font: str) -> bool:
    f = font.lower()
    return any(m in f for m in _MONO_MARKERS)


def _mime_from_name(name: str) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _MIME_BY_EXT.get(ext, "image/png")


def _dominant_font(chars: list[dict[str, Any]]) -> tuple[str, float]:
    if not chars:
        return ("", 0.0)
    names = Counter(str(c.get("fontname", "")) for c in chars)
    sizes = [float(c["size"]) for c in chars if c.get("size")]
    return (names.most_common(1)[0][0], median(sizes) if sizes else 0.0)


@dataclass
class _Line:
    top: float
    bottom: float
    text: str
    font: str
    size: float


def _classify(line: _Line, body_size: float) -> str:
    if _is_mono(line.font):
        return "code"
    if line.size >= body_size * _HEADING_FACTOR or (
        "bold" in line.font.lower() and line.size >= body_size * 1.05
    ):
        return "heading"
    return "body"


def _build_page_blocks(
    lines: list[_Line], tables: list[tuple[float, float, list[list[str]]]], body_size: float
) -> list[Block]:
    """Group classified lines + tables into ordered blocks for one page."""
    items: list[tuple[float, Block]] = [
        (top, TableBlock(rows=rows)) for top, _bottom, rows in tables
    ]
    table_ranges = [(top, bottom) for top, bottom, _rows in tables]

    text_lines = [ln for ln in lines if ln.text and not _in_tables(ln, table_ranges)]
    heights = [ln.bottom - ln.top for ln in text_lines]
    med_h = median(heights) if heights else 10.0

    para: list[str] = []
    para_top = 0.0
    prev_bottom: float | None = None
    code: list[str] = []
    code_top = 0.0

    def flush_para() -> None:
        nonlocal para
        if para:
            items.append((para_top, ParagraphBlock(text=" ".join(para))))
            para = []

    def flush_code() -> None:
        nonlocal code
        if code:
            items.append((code_top, CodeBlock(text="\n".join(code))))
            code = []

    for ln in text_lines:
        cls = _classify(ln, body_size)
        if cls == "heading":
            flush_para()
            flush_code()
            level = 1 if ln.size >= body_size * _H1_FACTOR else 2
            items.append((ln.top, HeadingBlock(level=level, text=ln.text)))
            prev_bottom = None
        elif cls == "code":
            flush_para()
            if not code:
                code_top = ln.top
            code.append(ln.text)
            prev_bottom = ln.bottom
        else:  # body
            flush_code()
            gap = ln.top - prev_bottom if prev_bottom is not None else 0.0
            if para and prev_bottom is not None and gap > _PARA_GAP_FACTOR * med_h:
                flush_para()
            if not para:
                para_top = ln.top
            para.append(ln.text)
            prev_bottom = ln.bottom
    flush_para()
    flush_code()

    items.sort(key=lambda pair: pair[0])
    return [block for _top, block in items]


def _in_tables(line: _Line, ranges: list[tuple[float, float]]) -> bool:
    mid = (line.top + line.bottom) / 2
    return any(top <= mid <= bottom for top, bottom in ranges)


class PdfParser:
    """Parses a PDF into the IR. Use :meth:`parse`, or ``extract`` + ``build`` to inject a TOC."""

    def parse(self, path: Path, *, toc: list[TocEntry] | None = None) -> Document:
        extracted = self.extract(path)
        chosen = toc if toc is not None else (extracted.outline or None)
        return self.build(extracted, chosen)

    def extract(self, path: Path) -> ExtractedPdf:
        try:
            raw_pages, table_data, page_texts = self._extract_text(path)
            metadata, outline, page_images = self._extract_meta_images(path)
        except ParseError:
            raise
        except Exception as exc:  # malformed/encrypted PDF
            raise ParseError(f"Could not parse PDF {path}: {exc}") from exc

        sizes = [ln.size for pg in raw_pages for ln in pg if ln.size and not _is_mono(ln.font)]
        body_size = median(sizes) if sizes else 11.0
        page_blocks = [
            _build_page_blocks(lines, tables, body_size)
            for lines, tables in zip(raw_pages, table_data, strict=True)
        ]
        return ExtractedPdf(
            metadata=metadata,
            page_blocks=page_blocks,
            page_images=page_images,
            outline=outline,
            page_texts=page_texts,
        )

    def _extract_text(
        self, path: Path
    ) -> tuple[list[list[_Line]], list[list[tuple[float, float, list[list[str]]]]], list[str]]:
        raw_pages: list[list[_Line]] = []
        table_data: list[list[tuple[float, float, list[list[str]]]]] = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                lines: list[_Line] = []
                for ln in page.extract_text_lines():
                    font, size = _dominant_font(ln.get("chars") or [])
                    lines.append(
                        _Line(
                            top=float(ln["top"]),
                            bottom=float(ln["bottom"]),
                            text=str(ln["text"]).strip(),
                            font=font,
                            size=size,
                        )
                    )
                tables: list[tuple[float, float, list[list[str]]]] = []
                for table in page.find_tables():
                    rows = [[(cell or "").strip() for cell in row] for row in table.extract()]
                    rows = [r for r in rows if any(r)]
                    if rows:
                        tables.append((float(table.bbox[1]), float(table.bbox[3]), rows))
                raw_pages.append(lines)
                table_data.append(tables)
        page_texts = ["\n".join(ln.text for ln in pg) for pg in raw_pages]
        return raw_pages, table_data, page_texts

    def _extract_meta_images(
        self, path: Path
    ) -> tuple[DocumentMetadata, list[TocEntry], list[list[tuple[str, ImageAsset]]]]:
        reader = PdfReader(str(path))
        info = reader.metadata
        title = (info.title if info and info.title else None) or path.stem
        author = info.author if info and info.author else None
        metadata = DocumentMetadata(
            title=str(title), author=str(author) if author else None, source_format="pdf"
        )

        outline: list[TocEntry] = []
        try:
            for item in reader.outline:
                if isinstance(item, list):
                    continue  # nested sub-entries: top-level chapters only for v1
                page_no = reader.get_destination_page_number(item)
                if page_no is not None:
                    outline.append(TocEntry(title=str(item.title), start_page=int(page_no)))
        except Exception:
            outline = []

        page_images: list[list[tuple[str, ImageAsset]]] = []
        for page in reader.pages:
            imgs: list[tuple[str, ImageAsset]] = []
            try:
                for image in page.images:
                    data = bytes(image.data)
                    digest = hashlib.sha256(data).hexdigest()[:16]
                    imgs.append(
                        (
                            digest,
                            ImageAsset(
                                image_id=f"pdfimg-{digest}",
                                data=data,
                                mime=_mime_from_name(str(image.name)),
                                original_path=str(image.name),
                            ),
                        )
                    )
            except Exception:
                imgs = []
            page_images.append(imgs)
        return metadata, outline, page_images

    def build(self, extracted: ExtractedPdf, toc: list[TocEntry] | None) -> Document:
        images: dict[str, ImageAsset] = {}
        seen_hashes: set[str] = set()

        def add_page_images(page_index: int, out: list[Block]) -> None:
            for digest, asset in extracted.page_images[page_index]:
                if digest in seen_hashes:
                    continue
                seen_hashes.add(digest)
                images[asset.image_id] = asset
                out.append(ImageBlock(image_id=asset.image_id))

        n_pages = len(extracted.page_blocks)
        chapters: list[Chapter] = []
        if not toc:
            blocks: list[Block] = []
            for page_index in range(n_pages):
                blocks.extend(extracted.page_blocks[page_index])
                add_page_images(page_index, blocks)
            chapters.append(Chapter(title=extracted.metadata.title, blocks=blocks))
        else:
            ordered = sorted(toc, key=lambda e: e.start_page)
            for idx, entry in enumerate(ordered):
                start = max(0, entry.start_page)
                end = ordered[idx + 1].start_page if idx + 1 < len(ordered) else n_pages
                chapter_blocks: list[Block] = []
                for page_index in range(start, min(end, n_pages)):
                    chapter_blocks.extend(extracted.page_blocks[page_index])
                    add_page_images(page_index, chapter_blocks)
                chapters.append(Chapter(title=entry.title, blocks=chapter_blocks))
        return Document(metadata=extracted.metadata, images=images, chapters=chapters)
