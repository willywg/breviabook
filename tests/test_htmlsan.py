"""Inline-HTML sanitizer (feat/inline-formatting)."""

from __future__ import annotations

from bs4 import BeautifulSoup

from breviabook.utils.htmlsan import (
    block_align,
    contains_markup,
    inline_tag_signature,
    list_marker,
    parse_class_styles,
    sanitize_inline,
    strip_tags,
)


def test_tags_normalize_to_semantic_allowlist() -> None:
    assert sanitize_inline("<i>a</i> <b>b</b>") == "<em>a</em> <strong>b</strong>"
    assert sanitize_inline("<em>x</em>") == "<em>x</em>"


def test_safe_link_kept_unsafe_scheme_dropped() -> None:
    assert sanitize_inline('<a href="https://x.com">t</a>') == '<a href="https://x.com">t</a>'
    assert sanitize_inline('<a href="javascript:evil()">t</a>') == "t"
    assert sanitize_inline('<a href="/internal.xhtml">t</a>') == "t"  # relative → unwrapped


def test_class_color_resolved_from_stylesheet() -> None:
    cs = parse_class_styles(".pdred1 { color: #9e0b0f } .ital { font-style: italic }")
    assert sanitize_inline('<span class="pdred1">Guiding</span>', cs) == (
        '<span style="color:#9e0b0f">Guiding</span>'
    )
    assert sanitize_inline('<span class="ital">scan</span>', cs) == "<em>scan</em>"


def test_inline_style_color_and_emphasis() -> None:
    assert (
        sanitize_inline('<span style="color: red">x</span>') == '<span style="color:red">x</span>'
    )
    assert sanitize_inline('<span style="font-weight:700">x</span>') == "<strong>x</strong>"


def test_grouped_and_commented_css_selectors() -> None:
    cs = parse_class_styles("/* c */ .a, .b { font-weight: bold }")
    assert "bold" in cs["a"] and "bold" in cs["b"]


def test_malicious_content_stripped() -> None:
    assert sanitize_inline('<span onclick="x()" style="color:red">ok</span>') == (
        '<span style="color:red">ok</span>'
    )
    assert sanitize_inline("<script>steal()</script>text") == "text"  # dropped, not surfaced
    assert sanitize_inline('<span style="color:url(evil)">x</span>') == "x"  # bad color rejected


def test_nested_emphasis_preserved() -> None:
    out = sanitize_inline("<strong>How we <em>really</em> use</strong>")
    assert out == "<strong>How we <em>really</em> use</strong>"


def test_strip_tags_and_contains_markup() -> None:
    rich = '<span style="color:red"><strong>Hi</strong></span> there'
    assert strip_tags(rich) == "Hi there"
    assert contains_markup(rich)
    assert not contains_markup("just text")


def test_signature_detects_tag_changes() -> None:
    src = '<strong>Don\'t</strong> <a href="https://x">think</a>'
    assert inline_tag_signature(sanitize_inline(src)) == inline_tag_signature(src)
    # dropping the link changes the signature
    assert inline_tag_signature("<strong>x</strong>") != inline_tag_signature(src)


def test_plain_text_stays_plain() -> None:
    assert sanitize_inline("no markup here") == "no markup here"
    assert not contains_markup(sanitize_inline("no markup here"))


def test_block_align_from_class_and_inline() -> None:
    cs = parse_class_styles(".center { text-align: center } .lefty { text-align: left }")
    node = BeautifulSoup('<p class="center">x</p>', "html.parser").p
    assert node is not None and block_align(node, cs) == "center"
    inline = BeautifulSoup('<p style="text-align:right">x</p>', "html.parser").p
    assert inline is not None and block_align(inline, cs) == "right"
    # Unsafe / unknown align values are ignored.
    bad = BeautifulSoup('<p style="text-align:justify">x</p>', "html.parser").p
    assert bad is not None and block_align(bad, cs) is None


def test_list_marker_type_and_color() -> None:
    cs = parse_class_styles(".redsq { list-style-type: square; color: #c00 }")
    node = BeautifulSoup('<ul class="redsq"><li>a</li></ul>', "html.parser").ul
    assert node is not None
    assert list_marker(node, cs) == ("square", "#c00")
    shorthand = BeautifulSoup('<ul style="list-style: circle outside"></ul>', "html.parser").ul
    assert shorthand is not None and list_marker(shorthand, {}) == ("circle", None)
    # Custom bullet image degrades to square.
    img = BeautifulSoup('<ul style="list-style-image: url(x.png)"></ul>', "html.parser").ul
    assert img is not None and list_marker(img, {}) == ("square", None)


def test_safe_link_schemes_unchanged_for_internal_hrefs() -> None:
    # Phase A must not widen the allowlist — relative / fragment hrefs stay unwrapped.
    assert sanitize_inline('<a href="#fn3">3</a>') == "3"
    assert sanitize_inline('<a href="chap.xhtml#x">t</a>') == "t"


def test_inline_image_resolved_and_signature() -> None:
    from breviabook.utils.htmlsan import inline_image_ids

    # With a resolver: <img src> becomes <img data-image-id>.
    def resolver(tag) -> str:
        return "img42"

    out = sanitize_inline('Omit <img src="x.png"/> words', img_resolver=resolver)
    assert out == 'Omit <img data-image-id="img42"/> words'
    assert inline_image_ids(out) == ["img42"]
    assert inline_tag_signature(out)["img:img42"] == 1

    # Without a resolver (translation-time): an existing data-image-id is kept, bare src dropped.
    assert sanitize_inline('a <img data-image-id="k"/> b') == 'a <img data-image-id="k"/> b'
    assert sanitize_inline('a <img src="x.png"/> b') == "a b"
