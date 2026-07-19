"""EPUB → IR parser, built on stdlib ``zipfile`` + ``lxml``/``beautifulsoup4``.

We deliberately do NOT use ``ebooklib`` (AGPL-3.0, ROADMAP §14). An EPUB is a ZIP holding
XHTML content plus an OPF package document that lists the manifest (every resource) and the
spine (reading order). We read ``META-INF/container.xml`` to find the OPF, parse it for
metadata + manifest + spine, then walk each spine XHTML into IR blocks, extracting images as
:class:`~breviabook.ir.models.ImageAsset` referenced by :class:`~breviabook.ir.models.ImageBlock`.

All archive-internal hrefs are resolved through ``breviabook.utils.security.resolve_archive_href``
to guard against zip-slip (ROADMAP §12). In-book links are remapped to opaque ``bbref:`` ids
(F1) so the rebuilt EPUB can rewrite them to output locations.
"""

from __future__ import annotations

import warnings
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup, Tag, XMLParsedAsHTMLWarning
from lxml import etree

from breviabook.ir.models import (
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
from breviabook.parsers.base import ParseError
from breviabook.utils.htmlsan import (
    Align,
    ClassStyles,
    HrefResolver,
    ImgResolver,
    block_align,
    contains_markup,
    list_marker,
    parse_class_styles,
    sanitize_inline,
    strip_tags,
)
from breviabook.utils.security import resolve_archive_href

# EPUB content is XHTML; parsing it with the lxml *HTML* parser is intentional and reliable here.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

_CONTAINER = "META-INF/container.xml"
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
# Block tags that may inherit ``text-align`` / ``anchor_id`` from a single-child wrapper (1 level).
# ``figure``/``img`` inherit align only (F7); they are not anchor-bearing blocks.
_ALIGNABLE = frozenset({"p", "blockquote", "figure", "img", *_HEADINGS})
_ANCHORABLE = frozenset({"p", "blockquote", *_HEADINGS, "ul", "ol"})
_SAFE_EXTERNAL = ("http://", "https://", "mailto:")


def _local(tag: str) -> str:
    """Strip an XML namespace from a tag name (``{ns}title`` -> ``title``)."""
    return tag.rsplit("}", 1)[-1].lower()


def _ancestor_align(node: Tag, class_styles: ClassStyles) -> Align | None:
    """Nearest ancestor's ``text-align`` (CSS inherits it to every descendant).

    Used only for images: a figure centered by its wrapper — e.g. ``<div class="image">`` around
    an ``<img>`` AND its ``<p class="caption">`` — must still center the image, even though the
    conservative text rule declines to inherit align onto a multi-child wrapper's paragraphs.
    """
    parent = node.parent
    while isinstance(parent, Tag) and parent.name.lower() != "body":
        align = block_align(parent, class_styles)
        if align is not None:
            return align
        parent = parent.parent
    return None


@dataclass
class _AnchorIndex:
    """Parse-only maps from source locations to opaque ``anchor_id`` values (``a1``, ``a2``, …)."""

    frag_map: dict[tuple[str, str], str] = field(default_factory=dict)
    chapter_map: dict[str, str] = field(default_factory=dict)
    # Paths whose chapter target was synthesized (no source id/heading) — attach to first block.
    pending_synthetic: dict[str, str] = field(default_factory=dict)
    _counter: int = 0

    def alloc(self) -> str:
        self._counter += 1
        return f"a{self._counter}"


class EpubParser:
    """Parses an ``.epub`` file into a :class:`~breviabook.ir.models.Document`."""

    def parse(self, path: Path) -> Document:
        if not zipfile.is_zipfile(path):
            raise ParseError(f"Not a valid EPUB (zip) file: {path}")
        with zipfile.ZipFile(path) as zf:
            opf_path = self._find_opf(zf)
            opf_xml = zf.read(opf_path)
            metadata, manifest, spine, cover_ref = self._parse_opf(opf_xml)
            class_styles = self._load_class_styles(manifest, opf_path, zf)

            spine_paths: list[str] = []
            for idref in spine:
                item = manifest.get(idref)
                if item is None or not _is_xhtml(item[1]):
                    continue
                try:
                    spine_paths.append(resolve_archive_href(opf_path, item[0]))
                except ValueError:
                    continue

            anchors = self._index_spine_anchors(spine_paths, zf)

            images: dict[str, ImageAsset] = {}
            chapters: list[Chapter] = []
            for href in spine_paths:
                try:
                    raw = zf.read(href)
                except KeyError:
                    continue
                chapters.append(
                    self._parse_chapter(
                        raw, href, opf_path, manifest, zf, images, class_styles, anchors
                    )
                )

            # OPF cover may not appear in any spine XHTML — load + mark it explicitly (F2).
            if cover_ref is not None and cover_ref in manifest:
                cover_href, _cover_mime = manifest[cover_ref]
                try:
                    cover_path = resolve_archive_href(opf_path, cover_href)
                except ValueError:
                    cover_path = None
                if cover_path is not None:
                    cover_id = self._register_asset(
                        cover_path, "Cover", manifest, opf_path, zf, images
                    )
                    if cover_id is not None:
                        metadata = metadata.model_copy(update={"cover_image_id": cover_id})

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
    ) -> tuple[DocumentMetadata, dict[str, tuple[str, str]], list[str], str | None]:
        root = etree.fromstring(opf_xml)
        title, author, language = "Untitled", None, None
        cover_manifest_id: str | None = None
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
            elif name == "meta":
                # EPUB2-style cover pointer: <meta name="cover" content="{manifest-id}"/>
                if el.get("name") == "cover":
                    content = el.get("content")
                    if isinstance(content, str) and content.strip():
                        cover_manifest_id = content.strip()
            elif name == "item":
                item_id, href, mtype = el.get("id"), el.get("href"), el.get("media-type")
                if item_id and href:
                    manifest[str(item_id)] = (str(href), str(mtype or ""))
            elif name == "itemref":
                idref = el.get("idref")
                if idref:
                    spine.append(str(idref))
        meta = DocumentMetadata(title=title, author=author, language=language, source_format="epub")
        return meta, manifest, spine, cover_manifest_id

    def _load_class_styles(
        self, manifest: dict[str, tuple[str, str]], opf_path: str, zf: zipfile.ZipFile
    ) -> ClassStyles:
        """Read every CSS resource once and merge its class → style rules (for color etc.)."""
        merged: ClassStyles = {}
        for item_href, mtype in manifest.values():
            if "css" not in mtype.lower():
                continue
            try:
                css = zf.read(resolve_archive_href(opf_path, item_href)).decode("utf-8", "ignore")
            except (KeyError, ValueError):
                continue
            for cls, rules in parse_class_styles(css).items():
                merged.setdefault(cls, {}).update(rules)
        return merged

    # -- internal anchors (F1) ----------------------------------------------- #

    def _index_spine_anchors(self, spine_paths: list[str], zf: zipfile.ZipFile) -> _AnchorIndex:
        """Pass 1: allocate opaque ids for every source ``id`` / ``a[name]`` and chapter targets."""
        index = _AnchorIndex()
        for path in spine_paths:
            try:
                raw = zf.read(path)
            except KeyError:
                continue
            soup = BeautifulSoup(raw, "lxml")
            body = soup.body or soup
            first_heading_aid: str | None = None
            first_any_aid: str | None = None
            for el in body.find_all(True):
                assert isinstance(el, Tag)
                frag = _source_frag(el)
                if frag is None:
                    continue
                key = (path, frag)
                if key in index.frag_map:
                    continue
                aid = index.alloc()
                index.frag_map[key] = aid
                if first_any_aid is None:
                    first_any_aid = aid
                if el.name.lower() in _HEADINGS and first_heading_aid is None:
                    first_heading_aid = aid
            if first_heading_aid is not None:
                index.chapter_map[path] = first_heading_aid
            elif first_any_aid is not None:
                index.chapter_map[path] = first_any_aid
            else:
                # File-only TOC hrefs with no ids/headings — synthetic chapter anchor.
                aid = index.alloc()
                index.chapter_map[path] = aid
                index.pending_synthetic[path] = aid
        return index

    def _make_href_resolver(self, chapter_path: str, index: _AnchorIndex) -> HrefResolver:
        """Map source hrefs to ``bbref:`` / external; never return archive-relative paths."""

        def resolve(href: str) -> str | None:
            if href.lower().startswith(_SAFE_EXTERNAL):
                return href
            if href.startswith("#"):
                frag = unquote(href[1:].split("?", 1)[0]).strip()
                if not frag:
                    return None
                aid = index.frag_map.get((chapter_path, frag))
                return f"bbref:{aid}" if aid is not None else None
            file_part, _, frag_part = href.partition("#")
            try:
                target = resolve_archive_href(chapter_path, file_part)
            except ValueError:
                return None
            frag = unquote(frag_part.split("?", 1)[0]).strip() if frag_part else ""
            if frag:
                aid = index.frag_map.get((target, frag))
                return f"bbref:{aid}" if aid is not None else None
            aid = index.chapter_map.get(target)
            return f"bbref:{aid}" if aid is not None else None

        return resolve

    # -- chapter / XHTML ----------------------------------------------------- #

    def _parse_chapter(
        self,
        raw: bytes,
        href: str,
        opf_path: str,
        manifest: dict[str, tuple[str, str]],
        zf: zipfile.ZipFile,
        images: dict[str, ImageAsset],
        class_styles: ClassStyles,
        anchors: _AnchorIndex,
    ) -> Chapter:
        soup = BeautifulSoup(raw, "lxml")
        body = soup.body or soup
        blocks: list[Block] = []
        img_resolver = self._make_img_resolver(href, opf_path, manifest, zf, images)
        href_resolver = self._make_href_resolver(href, anchors)
        self._walk(
            body,
            href,
            opf_path,
            manifest,
            zf,
            images,
            blocks,
            class_styles,
            img_resolver,
            href_resolver,
            anchors,
        )

        title: str | None = None
        for block in blocks:
            if isinstance(block, HeadingBlock):
                title = block.text
                break
        if title is None and soup.title and soup.title.string:
            title = soup.title.string.strip()
        return Chapter(title=title, blocks=blocks)

    def _make_img_resolver(
        self,
        href: str,
        opf_path: str,
        manifest: dict[str, tuple[str, str]],
        zf: zipfile.ZipFile,
        images: dict[str, ImageAsset],
    ) -> ImgResolver:
        """A resolver that registers an inline ``<img>``'s asset and returns its ``image_id``."""

        def resolve(img: Tag) -> str | None:
            src = img.get("src")
            if not isinstance(src, str) or not src:
                return None
            alt = img.get("alt")
            alt_str = alt if isinstance(alt, str) else None
            try:
                img_path = resolve_archive_href(href, src)
            except ValueError:
                return None
            return self._register_asset(img_path, alt_str, manifest, opf_path, zf, images)

        return resolve

    def _walk(
        self,
        node: Tag,
        href: str,
        opf_path: str,
        manifest: dict[str, tuple[str, str]],
        zf: zipfile.ZipFile,
        images: dict[str, ImageAsset],
        out: list[Block],
        class_styles: ClassStyles,
        img_resolver: ImgResolver,
        href_resolver: HrefResolver,
        anchors: _AnchorIndex,
        inherit_align: Align | None = None,
        inherit_anchor: str | None = None,
    ) -> None:
        for child in node.children:
            if not isinstance(child, Tag):
                continue
            tag = child.name.lower()
            if tag in _HEADINGS:
                text, rich = _rich_text(child, class_styles, img_resolver, href_resolver)
                if text:
                    align = block_align(child, class_styles) or inherit_align
                    aid = self._block_anchor_id(child, href, anchors, inherit_anchor, out)
                    out.append(
                        HeadingBlock(
                            level=_HEADINGS[tag],
                            text=text,
                            rich=rich,
                            align=align,
                            anchor_id=aid,
                        )
                    )
            elif tag == "pre":
                out.append(self._code_block(child))
            elif tag == "figure":
                self._emit_images(
                    child,
                    href,
                    opf_path,
                    manifest,
                    zf,
                    images,
                    out,
                    class_styles,
                    inherit_align,
                )
            elif tag == "img":
                self._add_image(
                    child,
                    href,
                    opf_path,
                    manifest,
                    zf,
                    images,
                    out,
                    class_styles,
                    inherit_align=inherit_align,
                )
            elif tag == "blockquote":
                text, rich = _rich_text(child, class_styles, img_resolver, href_resolver)
                if text:
                    align = block_align(child, class_styles) or inherit_align
                    aid = self._block_anchor_id(child, href, anchors, inherit_anchor, out)
                    out.append(QuoteBlock(text=text, rich=rich, align=align, anchor_id=aid))
            elif tag in ("ul", "ol"):
                lis = child.find_all("li", recursive=False)
                pairs = [
                    rt
                    for li in lis
                    if (rt := _rich_text(li, class_styles, img_resolver, href_resolver))[0]
                ]
                if pairs:
                    items = [t for t, _ in pairs]
                    riches = [r or t for t, r in pairs]
                    items_rich = riches if any(r for _, r in pairs) else None
                    marker_type, marker_color = list_marker(child, class_styles)
                    aid = self._block_anchor_id(child, href, anchors, inherit_anchor, out)
                    out.append(
                        ListBlock(
                            items=items,
                            ordered=tag == "ol",
                            items_rich=items_rich,
                            marker_type=marker_type,
                            marker_color=marker_color,
                            anchor_id=aid,
                        )
                    )
            elif tag == "table":
                out.append(self._table_block(child))
            elif tag == "p":
                text, rich = _rich_text(child, class_styles, img_resolver, href_resolver)
                if text:
                    align = block_align(child, class_styles) or inherit_align
                    aid = self._block_anchor_id(child, href, anchors, inherit_anchor, out)
                    out.append(ParagraphBlock(text=text, rich=rich, align=align, anchor_id=aid))
                elif child.find("img"):
                    self._emit_images(
                        child,
                        href,
                        opf_path,
                        manifest,
                        zf,
                        images,
                        out,
                        class_styles,
                        inherit_align,
                    )
            else:
                kids = [c for c in child.children if isinstance(c, Tag)]
                wrapper_align = block_align(child, class_styles)
                wrapper_anchor = _lookup_frag(child, href, anchors)
                single = len(kids) == 1
                child_tag = kids[0].name.lower() if single else ""
                if (wrapper_align is not None and single and child_tag in _ALIGNABLE) or (
                    wrapper_anchor is not None and single and child_tag in _ANCHORABLE
                ):
                    self._walk(
                        child,
                        href,
                        opf_path,
                        manifest,
                        zf,
                        images,
                        out,
                        class_styles,
                        img_resolver,
                        href_resolver,
                        anchors,
                        inherit_align=wrapper_align if wrapper_align is not None else inherit_align,
                        inherit_anchor=(
                            wrapper_anchor if wrapper_anchor is not None else inherit_anchor
                        ),
                    )
                else:
                    self._walk(
                        child,
                        href,
                        opf_path,
                        manifest,
                        zf,
                        images,
                        out,
                        class_styles,
                        img_resolver,
                        href_resolver,
                        anchors,
                    )

    def _block_anchor_id(
        self,
        el: Tag,
        chapter_path: str,
        anchors: _AnchorIndex,
        inherit_anchor: str | None,
        out: list[Block],
    ) -> str | None:
        aid = _lookup_frag(el, chapter_path, anchors) or inherit_anchor
        if aid is None and chapter_path in anchors.pending_synthetic:
            has_text = any(
                isinstance(b, (HeadingBlock, ParagraphBlock, QuoteBlock, ListBlock)) for b in out
            )
            if not has_text:
                # First text-bearing block in a chapter that needed a synthetic file-only target.
                aid = anchors.pending_synthetic.pop(chapter_path)
        return aid

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
        class_styles: ClassStyles,
        inherit_align: Align | None = None,
    ) -> None:
        caption_el = container.find("figcaption")
        caption = caption_el.get_text(" ", strip=True) if isinstance(caption_el, Tag) else None
        container_align = block_align(container, class_styles) or inherit_align
        for img in container.find_all("img"):
            self._add_image(
                img,
                href,
                opf_path,
                manifest,
                zf,
                images,
                out,
                class_styles,
                caption=caption,
                inherit_align=container_align,
            )

    def _add_image(
        self,
        img: Tag,
        href: str,
        opf_path: str,
        manifest: dict[str, tuple[str, str]],
        zf: zipfile.ZipFile,
        images: dict[str, ImageAsset],
        out: list[Block],
        class_styles: ClassStyles,
        caption: str | None = None,
        inherit_align: Align | None = None,
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
        align = (
            block_align(img, class_styles) or inherit_align or _ancestor_align(img, class_styles)
        )
        out.append(
            ImageBlock(
                image_id=image_id,
                caption=_clean_caption(caption or alt_str),
                align=align,
            )
        )

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


# Generic alt/caption placeholders that carry no information and only add noise as a caption.
_GENERIC_CAPTIONS = {"image", "img", "figure", "photo", "picture"}


def _source_frag(el: Tag) -> str | None:
    id_attr = el.get("id")
    if isinstance(id_attr, str) and id_attr.strip():
        return id_attr.strip()
    if el.name.lower() == "a":
        name_attr = el.get("name")
        if isinstance(name_attr, str) and name_attr.strip():
            return name_attr.strip()
    return None


def _lookup_frag(el: Tag, chapter_path: str, anchors: _AnchorIndex) -> str | None:
    frag = _source_frag(el)
    if frag is None:
        return None
    return anchors.frag_map.get((chapter_path, frag))


def _clean_caption(caption: str | None) -> str | None:
    """Drop generic placeholder captions (e.g. ``alt="Image"``) that add only noise."""
    if caption is None:
        return None
    if caption.strip().lower() in _GENERIC_CAPTIONS:
        return None
    return caption


def _rich_text(
    el: Tag,
    class_styles: ClassStyles,
    img_resolver: ImgResolver | None = None,
    href_resolver: HrefResolver | None = None,
) -> tuple[str, str | None]:
    """Return ``(plain_text, rich_or_None)`` for an inline-bearing element.

    ``rich`` is sanitized inline HTML kept only when the element actually carries markup; then
    ``text`` is its plain projection so the invariant ``text == strip_tags(rich)`` holds. A plain
    element keeps the original ``get_text`` behaviour and ``rich=None``.
    """
    rich = sanitize_inline(el, class_styles, img_resolver, href_resolver)
    if contains_markup(rich):
        return strip_tags(rich), rich
    return el.get_text(" ", strip=True), None


def _is_xhtml(media_type: str) -> bool:
    return "html" in media_type.lower()
