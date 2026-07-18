"""Shared block→HTML mapping used by the EPUB and PDF renderers.

The only difference between the two outputs is how an image's ``src`` is resolved (an archive
href for EPUB, a ``data:`` URI for PDF), so callers inject an ``image_src`` resolver.
In-book ``bbref:`` links are rewritten via an optional ``ref_resolve`` callback (F1).
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from typing import assert_never

from breviabook.ir.models import (
    Align,
    Block,
    Chapter,
    CodeBlock,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    ParagraphBlock,
    QuoteBlock,
    TableBlock,
)
from breviabook.utils.htmlsan import rewrite_bbrefs

ImageSrc = Callable[[str], str | None]
# Opaque anchor_id → output href (``#a1`` / ``chap-2.xhtml#a1``), or None to unwrap.
RefResolve = Callable[[str], str | None]

_INLINE_IMG_RE = re.compile(r'<img data-image-id="([^"]+)"/>')
_COLOR_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")

# Block HTML ids use opaque ``a{n}`` from the parser. These are disjoint from OPF/manifest
# NCNames the EPUB renderer allocates (``chap-*``, ``img-*``, ``nav``, ``cover-page``, ``toc``).


def esc(text: str) -> str:
    return html.escape(text, quote=True)


def _id_attr(anchor_id: str | None) -> str:
    if anchor_id is None:
        return ""
    return f' id="{esc(anchor_id)}"'


def _inline(
    text: str,
    rich: str | None,
    image_src: ImageSrc,
    ref_resolve: RefResolve | None = None,
) -> str:
    """Emit sanitized inline HTML (already safe) when present, else the escaped plain text.

    Inline ``<img data-image-id>`` placeholders are resolved to a real ``src`` (or dropped when
    the asset is gone). ``bbref:`` links are rewritten via ``ref_resolve`` or unwrapped.
    """
    if rich is None:
        return esc(text)

    def repl(m: re.Match[str]) -> str:
        src = image_src(m.group(1))
        return f'<img src="{src}" alt=""/>' if src else ""

    html_out = _INLINE_IMG_RE.sub(repl, rich)
    return rewrite_bbrefs(html_out, ref_resolve)


def _align_attr(align: Align | None) -> str:
    if align is None:
        return ""
    return f' style="text-align:{align}"'


def _marker_color_slug(color: str) -> str:
    slug = _COLOR_SLUG_RE.sub("", color)
    return slug or "x"


def _list_to_html(block: ListBlock, image_src: ImageSrc, ref_resolve: RefResolve | None) -> str:
    """Render a list; marker color uses ``li::marker`` (never ``color`` on the list element)."""
    tag = "ol" if block.ordered else "ul"
    riches = block.items_rich if block.items_rich is not None else [None] * len(block.items)
    items = "".join(
        f"<li>{_inline(item, rich, image_src, ref_resolve)}</li>"
        for item, rich in zip(block.items, riches, strict=True)
    )
    styles: list[str] = []
    if block.marker_type is not None:
        styles.append(f"list-style-type:{block.marker_type}")
    style_attr = f' style="{";".join(styles)}"' if styles else ""
    id_attr = _id_attr(block.anchor_id)
    if block.marker_color is None:
        return f"<{tag}{id_attr}{style_attr}>{items}</{tag}>"
    # Per-list class + rule so only the bullet/number is colored (no text bleed).
    slug = _marker_color_slug(block.marker_color)
    cls = f"bb-mc-{slug}"
    rule = f"<style>.{cls}>li::marker{{color:{esc(block.marker_color)}}}</style>"
    return f'{rule}<{tag} class="{cls}"{id_attr}{style_attr}>{items}</{tag}>'


def block_to_html(
    block: Block,
    image_src: ImageSrc,
    ref_resolve: RefResolve | None = None,
) -> str:
    """Render one IR block to an HTML/XHTML string.

    ``image_src`` maps an ``image_id`` to the value for ``<img src>`` (or ``None`` to skip).
    ``ref_resolve`` maps opaque ``anchor_id`` values to output hrefs (or ``None`` to unwrap).
    """
    if isinstance(block, HeadingBlock):
        return (
            f"<h{block.level}{_id_attr(block.anchor_id)}{_align_attr(block.align)}>"
            f"{_inline(block.text, block.rich, image_src, ref_resolve)}</h{block.level}>"
        )
    if isinstance(block, ParagraphBlock):
        return (
            f"<p{_id_attr(block.anchor_id)}{_align_attr(block.align)}>"
            f"{_inline(block.text, block.rich, image_src, ref_resolve)}</p>"
        )
    if isinstance(block, CodeBlock):
        cls = f' class="language-{esc(block.language)}"' if block.language else ""
        return f"<pre><code{cls}>{esc(block.text)}</code></pre>"
    if isinstance(block, QuoteBlock):
        return (
            f"<blockquote{_id_attr(block.anchor_id)}{_align_attr(block.align)}>"
            f"{_inline(block.text, block.rich, image_src, ref_resolve)}</blockquote>"
        )
    if isinstance(block, ListBlock):
        return _list_to_html(block, image_src, ref_resolve)
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


def collect_anchor_locations(chapters: list[Chapter]) -> dict[str, int]:
    """Map surviving ``anchor_id`` → 1-based chapter index in ``chapters`` (render order)."""
    locations: dict[str, int] = {}
    for index, chapter in enumerate(chapters, 1):
        for block in chapter.blocks:
            if (
                isinstance(block, (HeadingBlock, ParagraphBlock, QuoteBlock, ListBlock))
                and block.anchor_id is not None
                and block.anchor_id not in locations
            ):
                locations[block.anchor_id] = index
    return locations
