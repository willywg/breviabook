"""Inline-HTML sanitizer for the IR's ``rich`` fields (feat/inline-formatting).

Source EPUBs carry inline styling — emphasis, links, color — as a mix of real tags
(``<i> <b> <a>``) and CSS classes (Calibre's ``pdred1``). We keep that styling by storing a
**normalized, sanitized** inline-HTML string on each text block's ``rich`` field. Untrusted EPUB
markup must never reach our output verbatim, so this module is the single choke point: it maps
the source to a tiny semantic allowlist, escapes all text, drops everything else, and validates
attribute values. It runs on the way *in* (parser) and again on the LLM's translation output —
never trust a model to hand back clean tags.

Allowlist (everything else is unwrapped to its text):

    <em> <strong> <a href> <code> <sup> <sub> <s> <span style="color:…"> <br/>

produced from ``<i>/<em>``, ``<b>/<strong>``, ``<a>`` (http/https/mailto, or opaque ``bbref:``
after the parser remaps in-book links), inline ``<code>``, ``<sup>/<sub>``,
``<s>/<strike>/<del>``/``line-through``, ``color`` set inline or via a class, and void ``<br>``
(preserved as ``<br/>`` in ``rich``; ``strip_tags`` maps it to a space so the plain ``text``
projection for condensation is unchanged).

In-book ``#frag`` / relative XHTML hrefs are **not** allowlisted here — the EPUB parser rewrites
them to ``bbref:{anchor_id}`` first. ``_SAFE_LINK_SCHEMES`` stays external-only.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from collections.abc import Callable
from typing import Literal

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString, PageElement

# Effective styles a node can contribute. ``ClassStyles`` maps a CSS class name to a subset of
# {"italic","bold","strike","color","align","list-style-type"} extracted from the stylesheet.
ClassStyles = dict[str, dict[str, str]]
# Given an inline ``<img>`` tag, register its asset and return the ``image_id`` (or None to drop).
# Supplied by the parser; at translation time no resolver is used and existing ids are kept.
ImgResolver = Callable[[Tag], "str | None"]
# Rewrite a raw ``href`` to a safe value (external or ``bbref:…``), or None to unwrap the ``<a>``.
HrefResolver = Callable[[str], "str | None"]

Align = Literal["left", "center", "right"]
MarkerType = Literal["disc", "circle", "square", "none"]


_ITALIC_TAGS = {"i", "em"}
_BOLD_TAGS = {"b", "strong"}
_STRIKE_TAGS = {"s", "strike", "del"}
_SAFE_LINK_SCHEMES = ("http://", "https://", "mailto:")
# Opaque in-book refs produced by the EPUB parser (F1). Not added to _SAFE_LINK_SCHEMES.
_BBREF_RE = re.compile(r"^bbref:[A-Za-z][A-Za-z0-9_-]*$")
# Tags whose text content is dropped entirely (never surfaced, even as inert text).
_DROP_CONTENT = {"script", "style", "head", "title", "iframe", "object", "embed"}

# A color value we are willing to emit: #hex, rgb()/rgba(), or a bare CSS keyword. Anything with
# url(), expression, quotes, semicolons or comments is rejected outright.
_COLOR_RE = re.compile(r"^\s*(#[0-9a-fA-F]{3,8}|rgba?\([0-9.,%\s]+\)|[a-zA-Z]{3,20})\s*$")
_WS_RE = re.compile(r"\s+")
_ALIGN_MAP: dict[str, Align] = {"left": "left", "center": "center", "right": "right"}
_MARKER_MAP: dict[str, MarkerType] = {
    "disc": "disc",
    "circle": "circle",
    "square": "square",
    "none": "none",
}


def esc(text: str) -> str:
    return html.escape(text, quote=True)


def _safe_color(value: str) -> str | None:
    value = value.strip()
    if _COLOR_RE.match(value) and "url" not in value.lower() and "expression" not in value.lower():
        return value
    return None


def _normalize_marker_type(value: str) -> MarkerType | None:
    """Map a CSS list-style-type (or first token of list-style) to an IR marker, or None."""
    token = value.strip().lower().split(None, 1)[0] if value.strip() else ""
    # Custom bullet images are not modeled — degrade to square.
    if token.startswith("url("):
        return "square"
    return _MARKER_MAP.get(token)


def _parse_style_attr(style: str) -> dict[str, str]:
    """Pull style signals we care about out of an inline ``style`` string."""
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
        elif prop == "text-align" and val in _ALIGN_MAP:
            out["align"] = val
        elif prop == "list-style-type":
            marker = _normalize_marker_type(val)
            if marker is not None:
                out["list-style-type"] = marker
        elif prop == "list-style":
            # Shorthand: take the first recognizable type token (ignore position/image noise).
            for token in val.replace(",", " ").split():
                marker = _normalize_marker_type(token)
                if marker is not None:
                    out["list-style-type"] = marker
                    break
        elif prop == "list-style-image" and val not in {"", "none"}:
            out["list-style-type"] = "square"
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


def _safe_href_value(href: str) -> str | None:
    """Accept external schemes or opaque ``bbref:`` — never bare ``#`` / relative paths."""
    if href.lower().startswith(_SAFE_LINK_SCHEMES):
        return href
    if _BBREF_RE.fullmatch(href):
        return href
    return None


def _wrap(
    inner: str,
    node: Tag,
    class_styles: ClassStyles,
    href_resolver: HrefResolver | None,
) -> str:
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
        raw = node.get("href")
        if isinstance(raw, str):
            candidate = href_resolver(raw) if href_resolver is not None else raw
            if candidate is not None:
                safe = _safe_href_value(candidate)
                if safe is not None:
                    inner = f'<a href="{esc(safe)}">{inner}</a>'
    return inner


def _render_img(node: Tag, img_resolver: ImgResolver | None) -> str:
    """Emit ``<img data-image-id="ID"/>`` for an inline image, or drop it if unresolvable.

    At parse time ``img_resolver`` registers the asset (from ``src``) and returns its id. At
    translation time there is no resolver, so an already-normalized ``data-image-id`` is kept.
    """
    iid: str | None = None
    if img_resolver is not None:
        iid = img_resolver(node)
    else:
        existing = node.get("data-image-id")
        iid = existing if isinstance(existing, str) and existing else None
    return f'<img data-image-id="{esc(iid)}"/>' if iid else ""


def _render_child(
    node: PageElement,
    class_styles: ClassStyles,
    img_resolver: ImgResolver | None,
    href_resolver: HrefResolver | None,
) -> str:
    if isinstance(node, NavigableString):
        return esc(_WS_RE.sub(" ", str(node)))
    if isinstance(node, Tag):
        name = node.name.lower()
        if name in _DROP_CONTENT:
            return ""
        if name == "br":
            return "<br/>"
        if name == "img":
            return _render_img(node, img_resolver)
        inner = "".join(
            _render_child(c, class_styles, img_resolver, href_resolver) for c in node.children
        )
        return _wrap(inner, node, class_styles, href_resolver)
    return ""


def sanitize_inline(
    source: Tag | str,
    class_styles: ClassStyles | None = None,
    img_resolver: ImgResolver | None = None,
    href_resolver: HrefResolver | None = None,
) -> str:
    """Return normalized, sanitized inline HTML for ``source`` (a BS4 element or a raw string)."""
    cs = class_styles or {}
    if isinstance(source, str):
        source = BeautifulSoup(source, "html.parser")
    inner = "".join(_render_child(c, cs, img_resolver, href_resolver) for c in source.children)
    return _WS_RE.sub(" ", inner).strip()


def rewrite_bbrefs(rich: str, resolve: Callable[[str], str | None] | None) -> str:
    """Rewrite ``bbref:{id}`` hrefs via ``resolve``, or unwrap the ``<a>`` when unresolved.

    ``resolve`` maps an opaque anchor id (without the ``bbref:`` prefix) to an output href, or
    ``None`` to unwrap. When ``resolve`` itself is ``None``, every ``bbref:`` link is unwrapped
    (never leave ``bbref:`` in rendered output).
    """
    soup = BeautifulSoup(rich, "html.parser")
    changed = False
    for tag in list(soup.find_all("a")):
        href = tag.get("href")
        if not isinstance(href, str) or not href.startswith("bbref:"):
            continue
        aid = href.removeprefix("bbref:")
        new_href = resolve(aid) if resolve is not None else None
        if new_href is None:
            tag.unwrap()
        else:
            tag["href"] = new_href
        changed = True
    if not changed:
        return rich
    return soup.decode_contents()


def contains_markup(rich: str) -> bool:
    """True if ``rich`` actually carries a tag (else the block should keep ``rich=None``)."""
    return "<" in rich


def strip_tags(rich: str) -> str:
    """Plain-text projection of a rich string (the ``text`` fallback / invariant).

    ``<br>`` becomes a space so word boundaries survive without embedding newlines into the
    condensation path (which budgets on flattened prose).
    """
    soup = BeautifulSoup(rich, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with(" ")
    return _WS_RE.sub(" ", soup.get_text()).strip()


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
        elif name == "img":
            sig[f"img:{tag.get('data-image-id', '')}"] += 1
        else:
            sig[name] += 1
    return sig


def inline_image_ids(rich: str) -> list[str]:
    """Return the ``image_id``s referenced by inline ``<img>`` tags in a rich string."""
    soup = BeautifulSoup(rich, "html.parser")
    ids: list[str] = []
    for tag in soup.find_all("img"):
        iid = tag.get("data-image-id")
        if isinstance(iid, str) and iid:
            ids.append(iid)
    return ids


def _as_align(value: str | None) -> Align | None:
    return _ALIGN_MAP.get(value) if value is not None else None


def _as_marker_type(value: str | None) -> MarkerType | None:
    return _MARKER_MAP.get(value) if value is not None else None


def block_align(node: Tag, class_styles: ClassStyles | None = None) -> Align | None:
    """Resolve ``text-align`` on a block element from classes + inline style."""
    return _as_align(_effective_styles(node, class_styles or {}).get("align"))


def list_marker(
    node: Tag, class_styles: ClassStyles | None = None
) -> tuple[MarkerType | None, str | None]:
    """Resolve list marker type + color from a ``ul``/``ol`` element's classes + inline style.

    Color is the list element's own ``color`` (Calibre often colors bullets this way). Renderers
    must apply it via ``li::marker`` only — never ``color`` on the ``ul`` (that bleeds into text).
    """
    eff = _effective_styles(node, class_styles or {})
    marker = _as_marker_type(eff.get("list-style-type"))
    color = eff.get("color")
    if color is not None and _safe_color(color) is None:
        color = None
    return marker, color
