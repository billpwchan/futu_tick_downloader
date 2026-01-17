import random


class ExponentialBackoff:
    def __init__(self, min_delay: int, max_delay: int) -> None:
        self._min = max(1, min_delay)
        self._max = max(self._min, max_delay)
        self._current = self._min

    def reset(self) -> None:
        self._current = self._min

    def next_delay(self) -> int:
        delay = self._current
        self._current = min(self._current * 2, self._max)
        # small jitter avoids stampede on reconnect
        return max(self._min, int(delay + random.uniform(0, 1)))
