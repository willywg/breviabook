"""Inline-HTML sanitizer for the IR's ``rich`` fields (feat/inline-formatting).

Source EPUBs carry inline styling — emphasis, links, color — as a mix of real tags
(``<i> <b> <a>``) and CSS classes (Calibre's ``pdred1``). We keep that styling by storing a
**normalized, sanitized** inline-HTML string on each text block's ``rich`` field. Untrusted EPUB
markup must never reach our output verbatim, so this module is the single choke point: it maps
the source to a tiny semantic allowlist, escapes all text, drops everything else, and validates
attribute values. It runs on the way *in* (parser) and again on the LLM's translation output —
never trust a model to hand back clean tags.

Allowlist (everything else is unwrapped to its text):

    <em> <strong> <a href> <code> <sup> <sub> <s> <span style="color:…">

produced from ``<i>/<em>``, ``<b>/<strong>``, ``<a>`` (http/https/mailto only), inline ``<code>``,
``<sup>/<sub>``, ``<s>/<strike>/<del>``/``line-through``, and ``color`` set inline or via a class.
"""

from __future__ import annotations

import html
import re
from collections import Counter

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString, PageElement

# Effective styles a node can contribute. ``ClassStyles`` maps a CSS class name to a subset of
# {"italic","bold","strike","color"} extracted from the stylesheet.
ClassStyles = dict[str, dict[str, str]]

_ITALIC_TAGS = {"i", "em"}
_BOLD_TAGS = {"b", "strong"}
_STRIKE_TAGS = {"s", "strike", "del"}
_SAFE_LINK_SCHEMES = ("http://", "https://", "mailto:")
# Tags whose text content is dropped entirely (never surfaced, even as inert text).
_DROP_CONTENT = {"script", "style", "head", "title", "iframe", "object", "embed"}

# A color value we are willing to emit: #hex, rgb()/rgba(), or a bare CSS keyword. Anything with
# url(), expression, quotes, semicolons or comments is rejected outright.
_COLOR_RE = re.compile(r"^\s*(#[0-9a-fA-F]{3,8}|rgba?\([0-9.,%\s]+\)|[a-zA-Z]{3,20})\s*$")
_WS_RE = re.compile(r"\s+")


def esc(text: str) -> str:
    return html.escape(text, quote=True)


def _safe_color(value: str) -> str | None:
    value = value.strip()
    if _COLOR_RE.match(value) and "url" not in value.lower() and "expression" not in value.lower():
        return value
    return None


def _parse_style_attr(style: str) -> dict[str, str]:
    """Pull the four style signals we care about out of an inline ``style`` string."""
    out: dict[str, str] = {}
    for decl in style.split(";"):
        if ":" not in decl:
            continue
        prop, _, val = decl.partition(":")
        prop, val = prop.strip().lower(), val.strip().lower()
        if prop == "font-style" and "italic" in val:
            out["italic"] = "1"
        elif prop == "font-weight" and ("bold" in val or val in {"600", "700", "800", "900"}):
            out["bold"] = "1"
        elif prop == "text-decoration" and "line-through" in val:
            out["strike"] = "1"
        elif prop == "color":
            color = _safe_color(val)
            if color:
                out["color"] = color
    return out


def parse_class_styles(css_text: str) -> ClassStyles:
    """Extract per-class ``italic/bold/strike/color`` from stylesheet text (no CSS engine).

    Only simple single-class selectors (``.name``) are read; grouped selectors are split on
    commas and each simple ``.class`` among them inherits the rule. Complex selectors are ignored.
    """
    styles: ClassStyles = {}
    # Strip comments, then match `selectorlist { body }` blocks.
    css_text = re.sub(r"/\*.*?\*/", " ", css_text, flags=re.DOTALL)
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css_text):
        parsed = _parse_style_attr(body)
        if not parsed:
            continue
        for sel in selectors.split(","):
            sel = sel.strip()
            m = re.fullmatch(r"\.([A-Za-z_][\w-]*)", sel)
            if m:
                styles.setdefault(m.group(1), {}).update(parsed)
    return styles


def _classes(node: Tag) -> list[str]:
    cls = node.get("class")
    if isinstance(cls, list):
        return [c for c in cls if isinstance(c, str)]
    return [cls] if isinstance(cls, str) else []


def _effective_styles(node: Tag, class_styles: ClassStyles) -> dict[str, str]:
    """Combine tag name, class rules, and inline style into the node's effective styling."""
    eff: dict[str, str] = {}
    name = node.name.lower()
    if name in _ITALIC_TAGS:
        eff["italic"] = "1"
    if name in _BOLD_TAGS:
        eff["bold"] = "1"
    if name in _STRIKE_TAGS:
        eff["strike"] = "1"
    for cls in _classes(node):
        eff.update(class_styles.get(cls, {}))
    style = node.get("style")
    if isinstance(style, str):
        eff.update(_parse_style_attr(style))
    return eff


def _safe_href(node: Tag) -> str | None:
    href = node.get("href")
    if isinstance(href, str) and href.lower().startswith(_SAFE_LINK_SCHEMES):
        return href
    return None


def _wrap(inner: str, node: Tag, class_styles: ClassStyles) -> str:
    """Wrap already-sanitized ``inner`` in the semantic tags this node implies (fixed order)."""
    if not inner:
        return ""
    name = node.name.lower()
    eff = _effective_styles(node, class_styles)

    # innermost → outermost
    if name in ("sup", "sub"):
        inner = f"<{name}>{inner}</{name}>"
    if name == "code":
        inner = f"<code>{inner}</code>"
    if eff.get("strike"):
        inner = f"<s>{inner}</s>"
    if eff.get("italic"):
        inner = f"<em>{inner}</em>"
    if eff.get("bold"):
        inner = f"<strong>{inner}</strong>"
    if "color" in eff:
        inner = f'<span style="color:{esc(eff["color"])}">{inner}</span>'
    if name == "a":
        href = _safe_href(node)
        if href:
            inner = f'<a href="{esc(href)}">{inner}</a>'
    return inner


def _render_child(node: PageElement, class_styles: ClassStyles) -> str:
    if isinstance(node, NavigableString):
        return esc(_WS_RE.sub(" ", str(node)))
    if isinstance(node, Tag):
        name = node.name.lower()
        if name in _DROP_CONTENT:
            return ""
        if name == "br":
            return " "
        inner = "".join(_render_child(c, class_styles) for c in node.children)
        return _wrap(inner, node, class_styles)
    return ""


def sanitize_inline(source: Tag | str, class_styles: ClassStyles | None = None) -> str:
    """Return normalized, sanitized inline HTML for ``source`` (a BS4 element or a raw string)."""
    cs = class_styles or {}
    if isinstance(source, str):
        source = BeautifulSoup(source, "html.parser")
    inner = "".join(_render_child(c, cs) for c in source.children)
    return _WS_RE.sub(" ", inner).strip()


def contains_markup(rich: str) -> bool:
    """True if ``rich`` actually carries a tag (else the block should keep ``rich=None``)."""
    return "<" in rich


def strip_tags(rich: str) -> str:
    """Plain-text projection of a rich string (the ``text`` fallback / invariant)."""
    text = BeautifulSoup(rich, "html.parser").get_text()
    return _WS_RE.sub(" ", text).strip()


def inline_tag_signature(rich: str) -> Counter[str]:
    """Multiset of tags (with ``a``/``span`` attrs) — verifies a translation kept the markup."""
    soup = BeautifulSoup(rich, "html.parser")
    sig: Counter[str] = Counter()
    for tag in soup.find_all(True):
        name = tag.name.lower()
        if name == "a":
            sig[f"a:{tag.get('href', '')}"] += 1
        elif name == "span":
            sig[f"span:{tag.get('style', '')}"] += 1
        else:
            sig[name] += 1
    return sig
