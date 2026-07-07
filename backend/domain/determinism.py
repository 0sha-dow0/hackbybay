from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Protocol

_ID_ZERO_PAD_WIDTH = 8


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdGenerator(Protocol):
    def new_id(self, prefix: str) -> str: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock:
    def __init__(self, start: datetime, step_s: float) -> None:
        if start.utcoffset() is None:
            raise ValueError("FixedClock start must be timezone-aware")
        if not math.isfinite(step_s):
            raise ValueError("FixedClock step_s must be finite")
        if step_s < 0:
            raise ValueError("FixedClock step_s must be non-negative")
        self._current = start
        self._step = timedelta(seconds=step_s)

    def now(self) -> datetime:
        instant = self._current
        self._current = self._current + self._step
        return instant


class SequentialIdGenerator:
    def __init__(self, seed: int = 0) -> None:
        self._seed = seed
        self._counters: dict[str, int] = {}

    def new_id(self, prefix: str) -> str:
        if not prefix:
            raise ValueError("SequentialIdGenerator prefix must be non-empty")
        current = self._counters.get(prefix, self._seed)
        self._counters[prefix] = current + 1
        return f"{prefix}-{current:0{_ID_ZERO_PAD_WIDTH}d}"
