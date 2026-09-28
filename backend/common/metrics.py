"""In-process counters and latency histograms (per instance).

Tracks the RAG health signals separately so a quality regression is not hidden
behind aggregate latency: stage latencies, empty authorized results, citation
validation failures, authorization denials, provider errors and cache hits.
"""

import threading
from collections import defaultdict, deque
from typing import Dict

_WINDOW = 2000  # most recent samples kept per latency series


class _Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._counters: Dict[str, int] = defaultdict(int)
        self._latencies: Dict[str, deque] = defaultdict(lambda: deque(maxlen=_WINDOW))

    def incr(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] += value

    def observe_ms(self, name: str, ms: float) -> None:
        with self._lock:
            self._latencies[name].append(ms)

    @staticmethod
    def _pct(sorted_vals, p: float) -> float:
        if not sorted_vals:
            return 0.0
        idx = min(len(sorted_vals) - 1, int(round(p / 100 * (len(sorted_vals) - 1))))
        return round(sorted_vals[idx], 1)

    def snapshot(self) -> dict:
        with self._lock:
            latencies = {}
            for name, samples in self._latencies.items():
                vals = sorted(samples)
                latencies[name] = {
                    "count": len(vals),
                    "p50_ms": self._pct(vals, 50),
                    "p95_ms": self._pct(vals, 95),
                    "p99_ms": self._pct(vals, 99),
                }
            return {"counters": dict(self._counters), "latency": latencies}


metrics = _Metrics()
