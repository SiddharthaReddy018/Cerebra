"""Real-clock implementation of the Clock port."""

from __future__ import annotations

import datetime
import time


class RealClock:
    """Production clock. Inject this at the composition root."""

    def utcnow_iso(self) -> str:
        return datetime.datetime.now(datetime.UTC).isoformat()

    def utcnow_timestamp(self) -> float:
        return time.time()


class FakeClock:
    """Deterministic clock for tests. Inject instead of RealClock."""

    def __init__(self, timestamp: float = 0.0) -> None:
        self._ts = timestamp

    def utcnow_iso(self) -> str:
        return datetime.datetime.fromtimestamp(self._ts, tz=datetime.UTC).isoformat()

    def utcnow_timestamp(self) -> float:
        return self._ts

    def advance(self, seconds: float) -> None:
        self._ts += seconds

    def set(self, timestamp: float) -> None:
        self._ts = timestamp
