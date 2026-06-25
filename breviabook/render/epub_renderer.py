"""EPUB 3 renderer — our own builder on stdlib ``zipfile`` (no ebooklib, ROADMAP §14).

An EPUB is a ZIP with a (stored, first) ``mimetype`` entry, a ``META-INF/container.xml``
pointing at the OPF package document, the OPF (metadata + manifest + spine), an EPUB3
``nav.xhtml`` table of contents, one XHTML per chapter, and the embedded image assets. We
build each piece as a string and only re-embed images present in ``doc.images`` (the Strategy
A selector has already pruned dropped ones).

Output is deterministic (fixed ``dcterms:modified`` + a title-derived identifier) so the
IR round-trip test (render → parse) is stable (§11).
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path

from breviabook.ir.models import Chapter, Document
from breviabook.render.base import image_filename
from breviabook.render.html import block_to_html
from breviabook.render.html import esc as _esc

_MODIFIED = "2026-01-01T00:00:00Z"  # fixed for deterministic output
_NCNAME_BAD = re.compile(r"[^A-Za-z0-9._-]")

_CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def _xml_id(raw: str, used: set[str]) -> str:
    """Sanitize ``raw`` into a unique XML NCName for an OPF manifest id."""
    ident = _NCNAME_BAD.sub("_", raw) or "id"
    if not (ident[0].isalpha() or ident[0] == "_"):
        ident = f"id-{ident}"
    candidate = ident
    n = 1
    while candidate in used:
        n += 1
        candidate = f"{ident}-{n}"
    used.add(candidate)
    return candidate


class EpubRenderer:
    """Renders the IR to a valid EPUB 3 file."""

    name = "epub"

    def render(self, doc: Document, out_dir: Path, *, stem: str = "condensed-book") -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{stem}.epub"

        used_ids: set[str] = set()
        # Image assets -> (manifest id, archive href, filename), filenames deduped.
        image_entries: dict[str, tuple[str, str, str]] = {}
        used_names: set[str] = set()
        for image_id, asset in doc.images.items():
            filename = _unique_name(image_filename(asset), used_names)
            mid = _xml_id(f"img-{image_id}", used_ids)
            image_entries[image_id] = (mid, f"images/{filename}", filename)

        chapters = []
        for index, chapter in enumerate(doc.chapters, 1):
            cid = _xml_id(f"chap-{index}", used_ids)
            href = f"chap-{index}.xhtml"
            xhtml = self._chapter_xhtml(chapter.title or doc.metadata.title, chapter, image_entries)
            chapters.append((cid, href, chapter.title or f"Chapter {index}", xhtml))

        opf = self._build_opf(doc, chapters, image_entries)
        nav = self._build_nav(chapters)

        with zipfile.ZipFile(out_file, "w", zipfile.ZIP_DEFLATED) as zf:
            # mimetype MUST be first and stored uncompressed.
            zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", zipfile.ZIP_STORED)
            zf.writestr("META-INF/container.xml", _CONTAINER_XML)
            zf.writestr("OEBPS/content.opf", opf)
            zf.writestr("OEBPS/nav.xhtml", nav)
            for _cid, href, _title, xhtml in chapters:
                zf.writestr(f"OEBPS/{href}", xhtml)
            for image_id, (_mid, archive_href, _name) in image_entries.items():
                zf.writestr(f"OEBPS/{archive_href}", doc.images[image_id].data)
        return out_file

    # -- XHTML ---------------------------------------------------------------- #

    def _chapter_xhtml(
        self, title: str, chapter: Chapter, image_entries: dict[str, tuple[str, str, str]]
    ) -> str:
        def image_src(image_id: str) -> str | None:
            entry = image_entries.get(image_id)
            return entry[1] if entry is not None else None

        body = "\n".join(block_to_html(b, image_src) for b in chapter.blocks)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml">\n'
            f"<head><title>{_esc(title)}</title></head>\n"
            f"<body>\n{body}\n</body>\n</html>\n"
        )

    # -- OPF / nav ------------------------------------------------------------ #

    def _build_opf(
        self,
        doc: Document,
        chapters: list[tuple[str, str, str, str]],
        image_entries: dict[str, tuple[str, str, str]],
    ) -> str:
        meta = doc.metadata
        ident = "urn:breviabook:" + hashlib.sha256(meta.title.encode("utf-8")).hexdigest()[:16]
        lang = meta.language or "en"
        creator = f"\n    <dc:creator>{_esc(meta.author)}</dc:creator>" if meta.author else ""

        manifest = [
            '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        ]
        for cid, href, _title, _xhtml in chapters:
            manifest.append(f'<item id="{cid}" href="{href}" media-type="application/xhtml+xml"/>')
        for image_id, (mid, href, _name) in image_entries.items():
            mime = _esc(doc.images[image_id].mime or "application/octet-stream")
            manifest.append(f'<item id="{mid}" href="{href}" media-type="{mime}"/>')

        spine = "".join(f'<itemref idref="{cid}"/>' for cid, _h, _t, _x in chapters)
        manifest_xml = "\n    ".join(manifest)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
            'unique-identifier="bookid">\n'
            '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
            f'    <dc:identifier id="bookid">{ident}</dc:identifier>\n'
            f"    <dc:title>{_esc(meta.title)}</dc:title>\n"
            f"    <dc:language>{_esc(lang)}</dc:language>{creator}\n"
            f'    <meta property="dcterms:modified">{_MODIFIED}</meta>\n'
            "  </metadata>\n"
            f"  <manifest>\n    {manifest_xml}\n  </manifest>\n"
            f"  <spine>{spine}</spine>\n"
            "</package>\n"
        )

    def _build_nav(self, chapters: list[tuple[str, str, str, str]]) -> str:
        items = "".join(
            f'<li><a href="{href}">{_esc(title)}</a></li>' for _cid, href, title, _xhtml in chapters
        )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops">\n'
            "<head><title>Table of Contents</title></head>\n"
            '<body>\n<nav epub:type="toc" id="toc">\n'
            f"<h1>Contents</h1>\n<ol>{items}</ol>\n</nav>\n</body>\n</html>\n"
        )


def _unique_name(name: str, used: set[str]) -> str:
    if name not in used:
        used.add(name)
        return name
    stem, dot, ext = name.rpartition(".")
    base = stem if dot else name
    suffix = f".{ext}" if dot else ""
    n = 1
    while True:
        n += 1
        candidate = f"{base}-{n}{suffix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
