"""Shared Fingerprint helper: incremental SHA-1 over NUL-separated fields."""

from __future__ import annotations

import hashlib

from breviabook.persistence.fingerprint import Fingerprint


def _fp(*fields: str) -> str:
    fp = Fingerprint()
    for f in fields:
        fp.field(f)
    return fp.hexdigest()


def test_nul_separation_prevents_boundary_collisions() -> None:
    assert _fp("ab", "c") != _fp("a", "bc")


def test_field_order_matters() -> None:
    assert _fp("a", "b") != _fp("b", "a")


def test_empty_fields_are_significant() -> None:
    assert _fp("", "x") != _fp("x", "")
    assert _fp("x") != _fp("x", "")


def test_incremental_matches_single_shot() -> None:
    # The helper is just an incremental hasher over the canonical encoding: feeding fields
    # one by one must equal hashing their NUL-terminated concatenation directly.
    expected = hashlib.sha1(b"field1\0field2\0", usedforsecurity=False).hexdigest()
    assert _fp("field1", "field2") == expected


def test_unicode_fields() -> None:
    assert _fp("capítulo", "日本語") == _fp("capítulo", "日本語")
    assert _fp("capítulo") != _fp("capitulo")
