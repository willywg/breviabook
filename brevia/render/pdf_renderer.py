"""PDF renderer: IR → HTML → ``weasyprint`` (ROADMAP §4, §10 Phase 7).

We build one self-contained HTML document (images inlined as ``data:`` URIs) and let
weasyprint convert it to PDF. weasyprint (BSD) keeps us AGPL-free — we do NOT use PyMuPDF.

weasyprint depends on system libraries (Pango/GLib/cairo). To keep ``import`` safe where they
are absent, weasyprint is imported lazily inside :meth:`PdfRenderer.render`; the HTML builder
needs no system libs and is fully testable on its own.
"""

from __future__ import annotations

import base64
from pathlib import Path

from brevia.ir.models import Document, HeadingBlock
from brevia.render.html import block_to_html, esc

_CSS = """
@page { margin: 2cm; }
body { font-family: serif; line-height: 1.5; }
h1, h2, h3, h4, h5, h6 { font-family: sans-serif; }
section.chapter { page-break-before: always; }
section.chapter:first-of-type { page-break-before: avoid; }
pre { background: #f4f4f4; padding: 0.6em; border-radius: 4px; white-space: pre-wrap;
      font-family: monospace; font-size: 0.9em; }
code { font-family: monospace; }
figure { margin: 1em 0; text-align: center; }
img { max-width: 100%; }
figcaption { font-size: 0.85em; color: #555; }
table { border-collapse: collapse; margin: 1em 0; }
th, td { border: 1px solid #999; padding: 4px 8px; }
"""


def _data_uri(mime: str, data: bytes) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime or 'application/octet-stream'};base64,{encoded}"


def build_html(doc: Document) -> str:
    """Build a self-contained HTML document (images inlined) from the IR. Deterministic."""
    data_uris = {iid: _data_uri(asset.mime, asset.data) for iid, asset in doc.images.items()}

    def image_src(image_id: str) -> str | None:
        return data_uris.get(image_id)

    parts: list[str] = []
    if doc.metadata.title:
        parts.append(f'<h1 class="book-title">{esc(doc.metadata.title)}</h1>')
    for chapter in doc.chapters:
        parts.append('<section class="chapter">')
        first_is_heading = bool(chapter.blocks) and isinstance(chapter.blocks[0], HeadingBlock)
        if chapter.title and not first_is_heading:
            parts.append(f"<h2>{esc(chapter.title)}</h2>")
        for block in chapter.blocks:
            parts.append(block_to_html(block, image_src))
        parts.append("</section>")
    body = "\n".join(parts)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="' + esc(doc.metadata.language or "en") + '">\n'
        '<head><meta charset="utf-8">'
        f"<title>{esc(doc.metadata.title)}</title>"
        f"<style>{_CSS}</style></head>\n"
        f"<body>\n{body}\n</body>\n</html>\n"
    )


def weasyprint_available() -> bool:
    """True if weasyprint and its system libraries can be loaded."""
    try:
        import weasyprint  # noqa: F401
    except Exception:
        return False
    return True


class PdfRenderer:
    """Renders the IR to a PDF via weasyprint."""

    name = "pdf"

    def render(self, doc: Document, out_dir: Path, *, stem: str = "condensed-book") -> Path:
        from weasyprint import HTML  # lazy: avoids import-time system-lib dependency

        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{stem}.pdf"
        HTML(string=build_html(doc)).write_pdf(str(out_file))
        return out_file
