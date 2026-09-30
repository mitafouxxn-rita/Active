from pathlib import Path

import fakeredis
import pytest

from corpnorm.cache import (
    CacheStats,
    MemoryCache,
    NullCache,
    RedisCache,
    SQLiteCache,
    create_cache,
)
from corpnorm.ratelimit import NoRateLimit, RedisFixedWindowLimiter, TokenBucket


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


def test_null_cache() -> None:
    c = NullCache()
    c.set("a", "1")
    assert c.get("a") is None
    c.close()


def test_memory_cache_ttl_and_lru() -> None:
    clock = Clock()
    c = MemoryCache(maxsize=2, clock=clock)
    c.set("a", "1", ttl=10)
    c.set("b", "2")
    assert c.get("a") == "1"  # a が最近使われた
    c.set("c", "3")  # b が追い出される
    assert c.get("b") is None and len(c) == 2
    clock.t += 11
    assert c.get("a") is None
    assert c.get("missing") is None
    c.close()
    assert len(c) == 0


def test_sqlite_cache(tmp_path: Path) -> None:
    clock = Clock()
    path = tmp_path / "sub" / "c.db"
    c = SQLiteCache(path, clock=clock)
    c.set("k", "値", ttl=5)
    c.set("p", "永続")
    assert c.get("k") == "値"
    clock.t += 6
    assert c.get("k") is None
    c.set("x", "1", ttl=1)
    clock.t += 2
    assert c.purge_expired() == 1
    c.close()
    # 再オープンしても永続値は残る
    c2 = SQLiteCache(path, clock=clock)
    assert c2.get("p") == "永続"
    c2.close()


def test_redis_cache() -> None:
    client = fakeredis.FakeRedis(decode_responses=True)
    c = RedisCache(client)
    c.set("k", "v", ttl=60)
    assert c.get("k") == "v"
    assert client.ttl("corpnorm:k") > 0
    assert c.get("none") is None
    raw = RedisCache(fakeredis.FakeRedis())
    raw.set("b", "bytes")
    assert raw.get("b") == "bytes"
    c.close()


def test_redis_cache_failure_is_soft() -> None:
    class Broken:
        def get(self, *_a: object) -> None:
            raise ConnectionError("down")

        def set(self, *_a: object, **_k: object) -> None:
            raise ConnectionError("down")

        def close(self) -> None:
            pass

    c = RedisCache(Broken())
    c.set("a", "b")
    assert c.get("a") is None


def test_create_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(create_cache(None), NullCache)
    assert isinstance(create_cache("none"), NullCache)
    assert isinstance(create_cache("memory://"), MemoryCache)
    sc = create_cache(f"sqlite:///{tmp_path}/a.db")
    assert isinstance(sc, SQLiteCache) and sc.path == f"{tmp_path}/a.db"
    sc.close()
    monkeypatch.setattr(
        "redis.Redis.from_url", lambda url, **kw: fakeredis.FakeRedis(decode_responses=True)
    )
    assert isinstance(create_cache("redis://localhost:6379/0"), RedisCache)
    with pytest.raises(ValueError):
        create_cache("ftp://x")
    with pytest.raises(ValueError):
        create_cache("sqlite://")


def test_cache_stats() -> None:
    s = CacheStats()
    s.record(True)
    s.record(False)
    s.record(False)
    assert s.to_dict() == {"hits": 1, "misses": 2}


def test_token_bucket_throttles() -> None:
    clock = Clock()
    tb = TokenBucket(rate=2, burst=2, clock=clock, sleep=clock.sleep)
    start = clock.t
    for _ in range(6):
        tb.acquire()
    # バースト 2 回 + 残り 4 回を 2 回/秒 → 約 2 秒
    assert clock.t - start == pytest.approx(2.0, abs=0.01)


def test_token_bucket_try_acquire() -> None:
    clock = Clock()
    tb = TokenBucket(rate=1, clock=clock, sleep=clock.sleep)
    assert tb.try_acquire()
    assert not tb.try_acquire()
    clock.t += 1
    assert tb.try_acquire()
    with pytest.raises(ValueError):
        TokenBucket(0)


def test_redis_fixed_window() -> None:
    clock = Clock()
    client = fakeredis.FakeRedis()
    lim = RedisFixedWindowLimiter(client, rate=3, clock=clock, sleep=clock.sleep)
    for _ in range(3):
        lim.acquire()
    assert clock.t == 1000.0
    lim.acquire()  # 4 回目は次の窓まで待つ
    assert clock.t == pytest.approx(1001.0)
    with pytest.raises(ValueError):
        RedisFixedWindowLimiter(client, rate=0)
    NoRateLimit().acquire()
