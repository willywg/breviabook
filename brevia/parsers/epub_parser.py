"""EPUB → IR parser, built on stdlib ``zipfile`` + ``lxml``/``beautifulsoup4``.

We deliberately do NOT use ``ebooklib`` (AGPL-3.0, ROADMAP §14). An EPUB is a ZIP holding
XHTML content plus an OPF package document that lists the manifest (every resource) and the
spine (reading order). We read ``META-INF/container.xml`` to find the OPF, parse it for
metadata + manifest + spine, then walk each spine XHTML into IR blocks, extracting images as
:class:`~brevia.ir.models.ImageAsset` referenced by :class:`~brevia.ir.models.ImageBlock`.

All archive-internal hrefs are resolved through ``brevia.utils.security.resolve_archive_href``
to guard against zip-slip (ROADMAP §12).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from bs4 import BeautifulSoup, Tag
from lxml import etree

from brevia.ir.models import (
    Block,
    Chapter,
    CodeBlock,
    Document,
    DocumentMetadata,
    HeadingBlock,
    ImageAsset,
    ImageBlock,
    ListBlock,
    ParagraphBlock,
    QuoteBlock,
    TableBlock,
)
from brevia.parsers.base import ParseError
from brevia.utils.security import resolve_archive_href

_CONTAINER = "META-INF/container.xml"
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}


def _local(tag: str) -> str:
    """Strip an XML namespace from a tag name (``{ns}title`` -> ``title``)."""
    return tag.rsplit("}", 1)[-1].lower()


class EpubParser:
    """Parses an ``.epub`` file into a :class:`~brevia.ir.models.Document`."""

    def parse(self, path: Path) -> Document:
        if not zipfile.is_zipfile(path):
            raise ParseError(f"Not a valid EPUB (zip) file: {path}")
        with zipfile.ZipFile(path) as zf:
            opf_path = self._find_opf(zf)
            opf_xml = zf.read(opf_path)
            metadata, manifest, spine = self._parse_opf(opf_xml)

            images: dict[str, ImageAsset] = {}
            chapters: list[Chapter] = []
            for idref in spine:
                item = manifest.get(idref)
                if item is None or not _is_xhtml(item[1]):
                    continue
                href = resolve_archive_href(opf_path, item[0])
                try:
                    raw = zf.read(href)
                except KeyError:
                    continue
                chapters.append(self._parse_chapter(raw, href, opf_path, manifest, zf, images))

        return Document(metadata=metadata, images=images, chapters=chapters)

    # -- OPF / container ----------------------------------------------------- #

    def _find_opf(self, zf: zipfile.ZipFile) -> str:
        try:
            container = zf.read(_CONTAINER)
        except KeyError as exc:
            raise ParseError(f"EPUB missing {_CONTAINER}") from exc
        root = etree.fromstring(container)
        for el in root.iter():
            full_path = el.get("full-path")
            if _local(el.tag) == "rootfile" and full_path:
                return str(full_path)
        raise ParseError("EPUB container.xml has no rootfile full-path")

    def _parse_opf(
        self, opf_xml: bytes
    ) -> tuple[DocumentMetadata, dict[str, tuple[str, str]], list[str]]:
        root = etree.fromstring(opf_xml)
        title, author, language = "Untitled", None, None
        manifest: dict[str, tuple[str, str]] = {}  # id -> (href, media_type)
        spine: list[str] = []

        for el in root.iter():
            name = _local(el.tag)
            if name == "title" and el.text:
                title = el.text.strip()
            elif name == "creator" and el.text:
                author = el.text.strip()
            elif name == "language" and el.text:
                language = el.text.strip()
            elif name == "item":
                item_id, href, mtype = el.get("id"), el.get("href"), el.get("media-type")
                if item_id and href:
                    manifest[str(item_id)] = (str(href), str(mtype or ""))
            elif name == "itemref":
                idref = el.get("idref")
                if idref:
                    spine.append(str(idref))
        meta = DocumentMetadata(title=title, author=author, language=language, source_format="epub")
        return meta, manifest, spine

    # -- chapter / XHTML ----------------------------------------------------- #

    def _parse_chapter(
        self,
        raw: bytes,
        href: str,
        opf_path: str,
        manifest: dict[str, tuple[str, str]],
        zf: zipfile.ZipFile,
        images: dict[str, ImageAsset],
    ) -> Chapter:
        soup = BeautifulSoup(raw, "lxml")
        body = soup.body or soup
        blocks: list[Block] = []
        self._walk(body, href, opf_path, manifest, zf, images, blocks)

        title: str | None = None
        for block in blocks:
            if isinstance(block, HeadingBlock):
                title = block.text
                break
        if title is None and soup.title and soup.title.string:
            title = soup.title.string.strip()
        return Chapter(title=title, blocks=blocks)

    def _walk(
        self,
        node: Tag,
        href: str,
        opf_path: str,
        manifest: dict[str, tuple[str, str]],
        zf: zipfile.ZipFile,
        images: dict[str, ImageAsset],
        out: list[Block],
    ) -> None:
        for child in node.children:
            if not isinstance(child, Tag):
                continue
            tag = child.name.lower()
            if tag in _HEADINGS:
                text = child.get_text(" ", strip=True)
                if text:
                    out.append(HeadingBlock(level=_HEADINGS[tag], text=text))
            elif tag == "pre":
                out.append(self._code_block(child))
            elif tag == "figure":
                self._emit_images(child, href, opf_path, manifest, zf, images, out)
            elif tag == "img":
                self._add_image(child, href, opf_path, manifest, zf, images, out)
            elif tag == "blockquote":
                text = child.get_text(" ", strip=True)
                if text:
                    out.append(QuoteBlock(text=text))
            elif tag in ("ul", "ol"):
                lis = child.find_all("li", recursive=False)
                items = [t for li in lis if (t := li.get_text(" ", strip=True))]
                if items:
                    out.append(ListBlock(items=items, ordered=tag == "ol"))
            elif tag == "table":
                out.append(self._table_block(child))
            elif tag == "p":
                imgs = child.find_all("img")
                text = child.get_text(" ", strip=True)
                if imgs and not text:
                    self._emit_images(child, href, opf_path, manifest, zf, images, out)
                elif text:
                    out.append(ParagraphBlock(text=text))
            else:
                # Structural wrapper (div/section/body/...): recurse for nested blocks.
                self._walk(child, href, opf_path, manifest, zf, images, out)

    def _code_block(self, pre: Tag) -> CodeBlock:
        code_el = pre.find("code")
        target = code_el if isinstance(code_el, Tag) else pre
        language: str | None = None
        class_attr = target.get("class")
        classes = class_attr if isinstance(class_attr, list) else []
        for cls in classes:
            if isinstance(cls, str) and cls.startswith(("language-", "lang-")):
                language = cls.split("-", 1)[1]
                break
        return CodeBlock(language=language, text=target.get_text())

    def _table_block(self, table: Tag) -> TableBlock:
        rows: list[list[str]] = []
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
            if cells:
                rows.append(cells)
        return TableBlock(rows=rows)

    def _emit_images(
        self,
        container: Tag,
        href: str,
        opf_path: str,
        manifest: dict[str, tuple[str, str]],
        zf: zipfile.ZipFile,
        images: dict[str, ImageAsset],
        out: list[Block],
    ) -> None:
        caption_el = container.find("figcaption")
        caption = caption_el.get_text(" ", strip=True) if isinstance(caption_el, Tag) else None
        for img in container.find_all("img"):
            self._add_image(img, href, opf_path, manifest, zf, images, out, caption)

    def _add_image(
        self,
        img: Tag,
        href: str,
        opf_path: str,
        manifest: dict[str, tuple[str, str]],
        zf: zipfile.ZipFile,
        images: dict[str, ImageAsset],
        out: list[Block],
        caption: str | None = None,
    ) -> None:
        src = img.get("src")
        if not isinstance(src, str) or not src:
            return
        alt = img.get("alt")
        alt_str = alt if isinstance(alt, str) else None
        img_path = resolve_archive_href(href, src)
        image_id = self._register_asset(img_path, alt_str, manifest, opf_path, zf, images)
        if image_id is None:
            return
        out.append(ImageBlock(image_id=image_id, caption=(caption or alt_str) or None))

    def _register_asset(
        self,
        img_path: str,
        alt: str | None,
        manifest: dict[str, tuple[str, str]],
        opf_path: str,
        zf: zipfile.ZipFile,
        images: dict[str, ImageAsset],
    ) -> str | None:
        # Reuse the manifest id as a stable image_id; resolve mime from the manifest.
        image_id, mime = None, "application/octet-stream"
        for item_id, (item_href, item_mime) in manifest.items():
            if resolve_archive_href(opf_path, item_href) == img_path:
                image_id, mime = item_id, item_mime or mime
                break
        if image_id is None:
            image_id = img_path.rsplit("/", 1)[-1]  # fall back to filename
        if image_id in images:
            return image_id
        try:
            data = zf.read(img_path)
        except KeyError:
            return None
        images[image_id] = ImageAsset(
            image_id=image_id,
            data=data,
            mime=mime,
            original_path=img_path,
            alt_text=alt,
        )
        return image_id


def _is_xhtml(media_type: str) -> bool:
    return "html" in media_type.lower()
