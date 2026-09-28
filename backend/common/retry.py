"""Bounded retries with exponential backoff and full jitter."""

import asyncio
import logging
import random
import time
from typing import Awaitable, Callable, Optional, Tuple, Type, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")


def backoff_delay(attempt: int, base: float = 0.5, cap: float = 8.0) -> float:
    """Full-jitter delay for the given 1-based attempt number."""
    return random.uniform(0, min(cap, base * (2 ** (attempt - 1))))


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    deadline: Optional[float] = None,
    retry_on: Tuple[Type[BaseException], ...] = (Exception,),
    label: str = "operation",
) -> T:
    """Call `fn` until it succeeds, `attempts` is exhausted, or the monotonic
    `deadline` would be passed by the next sleep."""
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except retry_on as e:
            if attempt == attempts:
                raise
            delay = backoff_delay(attempt, base_delay, max_delay)
            if deadline is not None and time.monotonic() + delay >= deadline:
                raise
            logger.warning("%s failed (attempt %d/%d): %s; retrying in %.2fs", label, attempt, attempts, e, delay)
            await asyncio.sleep(delay)
    raise RuntimeError("unreachable")


async def retry_sync(fn: Callable[[], T], **kwargs) -> T:
    """Retry a blocking call, running each attempt in a worker thread."""
    return await retry_async(lambda: asyncio.to_thread(fn), **kwargs)
