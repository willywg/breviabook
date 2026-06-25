"""KeyPool: round-robin rotation over configured keys."""

from __future__ import annotations

from breviabook.llm.key_pool import KeyPool


def test_empty_pool_is_falsy() -> None:
    pool = KeyPool([])
    assert not pool
    assert len(pool) == 0
    assert pool.current is None
    assert pool.rotate() is None


def test_filters_blank_keys() -> None:
    pool = KeyPool(["", "  ", "k1", " k2 "])
    assert len(pool) == 2
    assert pool.current == "k1"


def test_round_robin() -> None:
    pool = KeyPool(["a", "b", "c"])
    assert pool.current == "a"
    assert pool.rotate() == "b"
    assert pool.rotate() == "c"
    assert pool.rotate() == "a"  # wraps


def test_single_key_rotate_stays() -> None:
    pool = KeyPool(["only"])
    assert pool.rotate() == "only"
