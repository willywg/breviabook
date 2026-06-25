"""with_key_rotation: failover across keys on auth/rate-limit errors."""

from __future__ import annotations

import pytest

from breviabook.llm.key_pool import KeyPool
from breviabook.llm.rate_limit import is_auth_error, is_rate_limit_error, with_key_rotation


class RateLimitError(Exception):
    pass


class AuthenticationError(Exception):
    pass


class OtherError(Exception):
    pass


async def _noop_sleep(_seconds: float) -> None:
    return None


def test_error_classification() -> None:
    assert is_rate_limit_error(RateLimitError())
    assert is_auth_error(AuthenticationError())
    assert not is_rate_limit_error(OtherError())
    assert not is_auth_error(OtherError())


async def test_success_first_try() -> None:
    pool = KeyPool(["a", "b"])
    used: list[str | None] = []

    async def call(key: str | None) -> str:
        used.append(key)
        return "ok"

    assert await with_key_rotation(pool, call, sleep=_noop_sleep) == "ok"
    assert used == ["a"]


async def test_auth_error_rotates_to_next_key() -> None:
    pool = KeyPool(["bad", "good"])
    used: list[str | None] = []

    async def call(key: str | None) -> str:
        used.append(key)
        if key == "bad":
            raise AuthenticationError
        return "ok"

    assert await with_key_rotation(pool, call, sleep=_noop_sleep) == "ok"
    assert used == ["bad", "good"]


async def test_rate_limit_rotates_and_sleeps() -> None:
    pool = KeyPool(["a", "b"])
    slept: list[float] = []
    calls = {"n": 0}

    async def call(key: str | None) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RateLimitError
        return "ok"

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    assert await with_key_rotation(pool, call, sleep=sleep) == "ok"
    assert calls["n"] == 2
    assert len(slept) == 1  # backed off once


async def test_rate_limit_bounded_by_max_retries() -> None:
    pool = KeyPool(["a", "b"])

    async def call(key: str | None) -> str:
        raise RateLimitError

    with pytest.raises(RateLimitError):
        await with_key_rotation(pool, call, max_retries=2, sleep=_noop_sleep)


async def test_non_retryable_propagates() -> None:
    pool = KeyPool(["a", "b"])

    async def call(key: str | None) -> str:
        raise OtherError("boom")

    with pytest.raises(OtherError, match="boom"):
        await with_key_rotation(pool, call, sleep=_noop_sleep)


async def test_auth_error_single_key_raises_immediately() -> None:
    pool = KeyPool(["only"])
    calls = {"n": 0}

    async def call(key: str | None) -> str:
        calls["n"] += 1
        raise AuthenticationError

    with pytest.raises(AuthenticationError):
        await with_key_rotation(pool, call, sleep=_noop_sleep)
    assert calls["n"] == 1  # no rotation possible
