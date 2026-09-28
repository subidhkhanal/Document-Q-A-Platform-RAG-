"""In-process sliding-window rate limits and a daily question budget.

Limits are per instance (App Runner may run several), which is enough to stop a
single visitor or script from burning provider credits on a public demo. A shared
store (e.g. Redis) would be needed for exact global limits.
"""

import threading
import time
from collections import defaultdict, deque
from datetime import date
from typing import Deque, Dict, Tuple

from fastapi import HTTPException, Request

from backend.common.metrics import metrics
from backend.config import DAILY_QA_BUDGET, RATE_LIMIT_ENABLED

_lock = threading.Lock()
_windows: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)
_daily: Dict[str, int] = {}


def client_ip(request: Request) -> str:
    """First hop of X-Forwarded-For (set by the App Runner load balancer), else the peer."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check(bucket: str, key: str, limit: int, window_seconds: float) -> None:
    """Raise 429 if `key` exceeded `limit` events in the window; otherwise record one."""
    if not RATE_LIMIT_ENABLED or limit <= 0:
        return
    now = time.monotonic()
    with _lock:
        events = _windows[(bucket, key)]
        while events and events[0] <= now - window_seconds:
            events.popleft()
        if len(events) >= limit:
            retry_after = max(1, int(window_seconds - (now - events[0])) + 1)
            metrics.incr(f"rate_limited.{bucket}")
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit reached. Try again in {retry_after} seconds.",
                headers={"Retry-After": str(retry_after)},
            )
        events.append(now)


def consume_daily_budget(bucket: str = "qa", budget: int = DAILY_QA_BUDGET) -> None:
    if not RATE_LIMIT_ENABLED or budget <= 0:
        return
    today = f"{bucket}:{date.today().isoformat()}"
    with _lock:
        for k in [k for k in _daily if k.startswith(f"{bucket}:") and k != today]:
            del _daily[k]
        if _daily.get(today, 0) >= budget:
            metrics.incr(f"budget_exhausted.{bucket}")
            raise HTTPException(status_code=429, detail="The demo's daily question budget is used up. Please come back tomorrow.")
        _daily[today] = _daily.get(today, 0) + 1


def reset() -> None:
    """Test helper."""
    with _lock:
        _windows.clear()
        _daily.clear()
