"""
Wall-clock latency instrumentation.

Context-manager timer that accumulates into a dict, with support for nested
sections. The graph passes one LatencyTimer per turn and every role records
its own slice; the API shim writes the final dict onto TurnResult.latency_ms.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Dict, Iterator


class LatencyTimer:
    def __init__(self) -> None:
        self.timings_ms: Dict[str, float] = {}
        self._turn_start = time.perf_counter()

    @contextmanager
    def section(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self.timings_ms[name] = round(
                self.timings_ms.get(name, 0.0) + elapsed_ms, 2
            )

    def finalize(self) -> Dict[str, float]:
        self.timings_ms["total"] = round(
            (time.perf_counter() - self._turn_start) * 1000.0, 2
        )
        return self.timings_ms
