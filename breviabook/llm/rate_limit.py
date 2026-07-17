"""Rate-limit / auth failover via key rotation (ROADMAP §7.4).

``with_key_rotation`` retries an async call across a :class:`~breviabook.llm.key_pool.KeyPool`:
- on an authentication error, rotate to the next key (a dead key shouldn't kill the job);
- on a rate-limit error, rotate and back off (injected ``sleep`` keeps it testable);
- any other error propagates immediately.

Errors are matched by class name across the MRO, so litellm's exceptions and lightweight test
doubles are both recognized without importing litellm here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from breviabook.llm.key_pool import KeyPool

T = TypeVar("T")

_RATE_LIMIT_NAMES = {"RateLimitError", "Timeout", "APIConnectionError"}
_AUTH_NAMES = {"AuthenticationError", "PermissionDeniedError"}


def _mro_names(exc: BaseException) -> set[str]:
    return {cls.__name__ for cls in type(exc).__mro__}


def is_rate_limit_error(exc: BaseException) -> bool:
    return bool(_mro_names(exc) & _RATE_LIMIT_NAMES)


def is_auth_error(exc: BaseException) -> bool:
    return bool(_mro_names(exc) & _AUTH_NAMES)


async def retry_with_backoff(
    call: Callable[[], Awaitable[T]],
    *,
    max_retries: int = 3,
    backoff_base: float = 0.5,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    is_retryable: Callable[[BaseException], bool] = is_rate_limit_error,
) -> T:
    """Run ``call()`` with exponential backoff on transient rate-limit/connection errors."""
    attempts = 0
    while True:
        try:
            return await call()
        except Exception as exc:
            if is_retryable(exc) and attempts < max_retries:
                await sleep(backoff_base * (2**attempts))
                attempts += 1
                continue
            raise


async def with_key_rotation(
    pool: KeyPool,
    call: Callable[[str | None], Awaitable[str]],
    *,
    max_retries: int = 3,
    backoff_base: float = 0.5,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Run ``call(current_key)``, rotating the pool on auth/rate-limit failures."""
    attempts = 0
    auth_rotations = 0
    while True:
        try:
            return await call(pool.current)
        except Exception as exc:
            if is_auth_error(exc) and auth_rotations < len(pool) - 1:
                pool.rotate()
                auth_rotations += 1
                continue
            if is_rate_limit_error(exc) and attempts < max_retries:
                pool.rotate()
                await sleep(backoff_base * (2**attempts))
                attempts += 1
                continue
            raise
