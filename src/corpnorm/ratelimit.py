"""外部 API 呼び出しのレート制限。

* :class:`TokenBucket` — プロセス内（スレッド間共有）
* :class:`RedisFixedWindowLimiter` — 複数プロセス・複数ホストで上限を共有
"""

from __future__ import annotations

import threading
import time
from typing import Any, Protocol


class RateLimiter(Protocol):
    def acquire(self) -> None: ...


class NoRateLimit:
    def acquire(self) -> None:
        return None


class TokenBucket:
    """1 秒あたり ``rate`` 回、最大 ``burst`` 回まで連続実行を許可する。"""

    def __init__(
        self,
        rate: float,
        burst: int | None = None,
        *,
        clock: Any = time.monotonic,
        sleep: Any = time.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate は正の数で指定してください")
        self.rate = rate
        self.capacity = float(burst if burst is not None else max(1, int(rate)))
        self._tokens = self.capacity
        self._clock = clock
        self._sleep = sleep
        self._updated = clock()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
        self._updated = now

    def try_acquire(self) -> bool:
        with self._lock:
            self._refill()
            if self._tokens >= 1:
                self._tokens -= 1
                return True
            return False

    def acquire(self) -> None:
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait = (1 - self._tokens) / self.rate
            self._sleep(wait)


class RedisFixedWindowLimiter:
    """Redis の INCR + EXPIRE による 1 秒窓の分散レート制限。"""

    def __init__(
        self,
        client: Any,
        rate: int,
        key: str = "corpnorm:ratelimit:houjin",
        *,
        clock: Any = time.time,
        sleep: Any = time.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate は正の整数で指定してください")
        self._client = client
        self.rate = rate
        self.key = key
        self._clock = clock
        self._sleep = sleep

    def acquire(self) -> None:
        while True:
            now = self._clock()
            window = int(now)
            k = f"{self.key}:{window}"
            pipe = self._client.pipeline()
            pipe.incr(k)
            pipe.expire(k, 2)
            count, _ = pipe.execute()
            if int(count) <= self.rate:
                return
            self._sleep(max(0.001, window + 1 - now))
