"""Renderer abstraction — every output format consumes a (condensed) ``Document``.

Renderers are the mirror of parsers (ROADMAP §3, §5 step 7): IR in, file out. Markdown
(Phase 2), EPUB (Phase 6), and PDF (Phase 7) all implement this Protocol.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path
from typing import Protocol

from breviabook.ir.models import Document, ImageAsset

_MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
    "image/webp": ".webp",
}
_SAFE = re.compile(r"[^A-Za-z0-9._-]")


class Renderer(Protocol):
    """Renders a :class:`~breviabook.ir.models.Document` into ``out_dir``; returns the main file."""

    name: str

    def render(self, doc: Document, out_dir: Path, *, stem: str = ...) -> Path: ...


def image_filename(asset: ImageAsset) -> str:
    """Derive a stable, filesystem-safe filename for ``asset``.

    Prefers the basename of the asset's original archive path; otherwise uses the image id
    plus an extension inferred from the MIME type.
    """
    if asset.original_path:
        name = posixpath.basename(asset.original_path)
    else:
        name = asset.image_id + _MIME_EXT.get(asset.mime.lower(), ".bin")
    name = _SAFE.sub("_", name).lstrip(".") or "image"
    if "." not in name:
        name += _MIME_EXT.get(asset.mime.lower(), ".bin")
    return name
