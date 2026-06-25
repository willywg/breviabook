"""Shared block→HTML mapping used by the EPUB and PDF renderers.

The only difference between the two outputs is how an image's ``src`` is resolved (an archive
href for EPUB, a ``data:`` URI for PDF), so callers inject an ``image_src`` resolver.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from typing import assert_never

from breviabook.ir.models import (
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


def esc(text: str) -> str:
    return html.escape(text, quote=True)


def block_to_html(block: Block, image_src: ImageSrc) -> str:
    """Render one IR block to an HTML/XHTML string.

    ``image_src`` maps an ``image_id`` to the value for ``<img src>`` (or ``None`` to skip).
    """
    if isinstance(block, HeadingBlock):
        return f"<h{block.level}>{esc(block.text)}</h{block.level}>"
    if isinstance(block, ParagraphBlock):
        return f"<p>{esc(block.text)}</p>"
    if isinstance(block, CodeBlock):
        cls = f' class="language-{esc(block.language)}"' if block.language else ""
        return f"<pre><code{cls}>{esc(block.text)}</code></pre>"
    if isinstance(block, QuoteBlock):
        return f"<blockquote>{esc(block.text)}</blockquote>"
    if isinstance(block, ListBlock):
        tag = "ol" if block.ordered else "ul"
        items = "".join(f"<li>{esc(item)}</li>" for item in block.items)
        return f"<{tag}>{items}</{tag}>"
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
