"""Small in-process TTL + LRU caches.

The retrieval cache key always includes the caller's authorization-scope
fingerprint (readable documents and their active versions), which is resolved
fresh from PostgreSQL before every lookup. A permission change, new version or
deletion therefore changes the key and can never be served a stale result.
"""

import hashlib
import json
import re
import time
from collections import OrderedDict
from typing import Any, Hashable, Optional


class TTLCache:
    def __init__(self, max_entries: int, ttl_seconds: float):
        self.max_entries = max_entries
        self.ttl = ttl_seconds
        self._data: "OrderedDict[Hashable, tuple[float, Any, Any]]" = OrderedDict()

    def get(self, key: Hashable) -> Optional[Any]:
        item = self._data.get(key)
        if item is None:
            return None
        expires, _, value = item
        if expires < time.monotonic():
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key: Hashable, value: Any, tag: Any = None) -> None:
        self._data[key] = (time.monotonic() + self.ttl, tag, value)
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)

    def invalidate_tag(self, tag: Any) -> int:
        stale = [k for k, (_, t, _) in self._data.items() if t == tag]
        for k in stale:
            self._data.pop(k, None)
        return len(stale)

    def clear(self) -> None:
        self._data.clear()


def normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", query.strip().lower())


def cache_key(**parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()
