"""Retry with exponential backoff (spec §6.4 hardening / §1.4 rate limits).

Kite historical/quote APIs are rate-limited; transient errors should back off and retry,
not crash the loop. The sleep function is injectable so tests run instantly.
"""
from __future__ import annotations

import time
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")


def call_with_retries(fn: Callable[[], T], *,
                      retries: int = 3,
                      base_delay: float = 0.5,
                      max_delay: float = 8.0,
                      exceptions: tuple[type[BaseException], ...] = (Exception,),
                      sleep: Callable[[float], None] = time.sleep,
                      on_retry: Callable[[int, BaseException, float], None] | None = None) -> T:
    """Call `fn`, retrying up to `retries` times on `exceptions` with exponential backoff
    (base_delay × 2**attempt, capped at max_delay). Re-raises the last error if all fail."""
    attempt = 0
    while True:
        try:
            return fn()
        except exceptions as e:  # noqa: BLE001 — intentional, configurable
            if attempt >= retries:
                raise
            delay = min(base_delay * (2 ** attempt), max_delay)
            if on_retry is not None:
                on_retry(attempt + 1, e, delay)
            sleep(delay)
            attempt += 1


def retry(**kwargs):
    """Decorator form of call_with_retries."""
    def decorator(fn):
        def wrapper(*args, **kw):
            return call_with_retries(lambda: fn(*args, **kw), **kwargs)
        return wrapper
    return decorator
