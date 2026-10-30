"""Rolling-window rate limiter: чистая логика по переданному времени (TD-0039, ADR-0001)."""

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_s: float | None = None


class RollingWindowLimiter:
    """Не более max_calls событий за window_s: окно скользит по времени событий.

    check() без await внутри — атомарен в однопоточном event loop. Отказ не
    записывается в окно: повтор после retry_after_s проходит.
    """

    def __init__(self, max_calls: int, window_s: float) -> None:
        if max_calls < 1:
            raise ValueError("max_calls >= 1")
        if window_s <= 0:
            raise ValueError("window_s > 0")
        self._max_calls = max_calls
        self._window_s = window_s
        self._seen: deque[float] = deque()

    def check(self, now: float) -> RateLimitDecision:
        horizon = now - self._window_s
        while self._seen and self._seen[0] <= horizon:
            self._seen.popleft()
        if len(self._seen) >= self._max_calls:
            retry_after = self._window_s - (now - self._seen[0])
            return RateLimitDecision(False, max(retry_after, 0.0))
        self._seen.append(now)
        return RateLimitDecision(True)
