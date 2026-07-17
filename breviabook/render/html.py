"""Shared block→HTML mapping used by the EPUB and PDF renderers.

The only difference between the two outputs is how an image's ``src`` is resolved (an archive
href for EPUB, a ``data:`` URI for PDF), so callers inject an ``image_src`` resolver.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from typing import assert_never

from breviabook.ir.models import (
    Align,
    Block,
    CodeBlock,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    ParagraphBlock,
    QuoteBlock,
    TableBlock,
)

ImageSrc = Callable[[str], str | None]

_INLINE_IMG_RE = re.compile(r'<img data-image-id="([^"]+)"/>')
_COLOR_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def esc(text: str) -> str:
    return html.escape(text, quote=True)


def _inline(text: str, rich: str | None, image_src: ImageSrc) -> str:
    """Emit sanitized inline HTML (already safe) when present, else the escaped plain text.

    Inline ``<img data-image-id>`` placeholders are resolved to a real ``src`` (or dropped when
    the asset is gone).
    """
    if rich is None:
        return esc(text)

    def repl(m: re.Match[str]) -> str:
        src = image_src(m.group(1))
        return f'<img src="{src}" alt=""/>' if src else ""

    return _INLINE_IMG_RE.sub(repl, rich)


def _align_attr(align: Align | None) -> str:
    if align is None:
        return ""
    return f' style="text-align:{align}"'


def _marker_color_slug(color: str) -> str:
    slug = _COLOR_SLUG_RE.sub("", color)
    return slug or "x"


def _list_to_html(block: ListBlock, image_src: ImageSrc) -> str:
    """Render a list; marker color uses ``li::marker`` (never ``color`` on the list element)."""
    tag = "ol" if block.ordered else "ul"
    riches = block.items_rich if block.items_rich is not None else [None] * len(block.items)
    items = "".join(
        f"<li>{_inline(item, rich, image_src)}</li>"
        for item, rich in zip(block.items, riches, strict=True)
    )
    styles: list[str] = []
    if block.marker_type is not None:
        styles.append(f"list-style-type:{block.marker_type}")
    style_attr = f' style="{";".join(styles)}"' if styles else ""
    if block.marker_color is None:
        return f"<{tag}{style_attr}>{items}</{tag}>"
    # Per-list class + rule so only the bullet/number is colored (no text bleed).
    slug = _marker_color_slug(block.marker_color)
    cls = f"bb-mc-{slug}"
    rule = f"<style>.{cls}>li::marker{{color:{esc(block.marker_color)}}}</style>"
    return f'{rule}<{tag} class="{cls}"{style_attr}>{items}</{tag}>'


def block_to_html(block: Block, image_src: ImageSrc) -> str:
    """Render one IR block to an HTML/XHTML string.

    ``image_src`` maps an ``image_id`` to the value for ``<img src>`` (or ``None`` to skip).
    """
    if isinstance(block, HeadingBlock):
        return (
            f"<h{block.level}{_align_attr(block.align)}>"
            f"{_inline(block.text, block.rich, image_src)}</h{block.level}>"
        )
    if isinstance(block, ParagraphBlock):
        return f"<p{_align_attr(block.align)}>{_inline(block.text, block.rich, image_src)}</p>"
    if isinstance(block, CodeBlock):
        cls = f' class="language-{esc(block.language)}"' if block.language else ""
        return f"<pre><code{cls}>{esc(block.text)}</code></pre>"
    if isinstance(block, QuoteBlock):
        return (
            f"<blockquote{_align_attr(block.align)}>"
            f"{_inline(block.text, block.rich, image_src)}</blockquote>"
        )
    if isinstance(block, ListBlock):
        return _list_to_html(block, image_src)
    if isinstance(block, TableBlock):
        rows = []
        for r_index, row in enumerate(block.rows):
            cell = "th" if r_index == 0 else "td"
            cells = "".join(f"<{cell}>{esc(c)}</{cell}>" for c in row)
            rows.append(f"<tr>{cells}</tr>")
        return f"<table>{''.join(rows)}</table>"
    if isinstance(block, ImageBlock):
        src = image_src(block.image_id)
        if src is None:
            return ""
        alt = esc(block.caption or "")
        caption = f"<figcaption>{esc(block.caption)}</figcaption>" if block.caption else ""
        return f'<figure><img src="{src}" alt="{alt}"/>{caption}</figure>'
    assert_never(block)
