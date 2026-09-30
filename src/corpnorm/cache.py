"""国税庁 API レスポンスのキャッシュ。

* ``memory://``          プロセス内 LRU（REST API の単一ワーカー向け）
* ``sqlite:///path.db``  ローカル永続キャッシュ（CLI・夜間バッチの再実行向け）
* ``redis://host:6379/0`` 複数ワーカー・複数ホストで共有
* ``none://``            キャッシュ無効

値は JSON 文字列、TTL は秒単位。キャッシュ障害は処理を止めずに警告ログに留める。
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class Cache(Protocol):
    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str, ttl: int | None = None) -> None: ...

    def close(self) -> None: ...


class CacheStats:
    def __init__(self) -> None:
        self.hits = 0
        self.misses = 0
        self._lock = threading.Lock()

    def record(self, hit: bool) -> None:
        with self._lock:
            if hit:
                self.hits += 1
            else:
                self.misses += 1

    def to_dict(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses}


class NullCache:
    def get(self, key: str) -> str | None:
        return None

    def set(self, key: str, value: str, ttl: int | None = None) -> None:
        return None

    def close(self) -> None:
        return None


class MemoryCache:
    """スレッドセーフな TTL 付き LRU キャッシュ。"""

    def __init__(self, maxsize: int = 10_000, clock: Any = time.monotonic) -> None:
        self.maxsize = maxsize
        self._clock = clock
        self._data: OrderedDict[str, tuple[float | None, str]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires, value = item
            if expires is not None and expires <= self._clock():
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: str, value: str, ttl: int | None = None) -> None:
        expires = self._clock() + ttl if ttl else None
        with self._lock:
            self._data[key] = (expires, value)
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def __len__(self) -> int:
        return len(self._data)

    def close(self) -> None:
        with self._lock:
            self._data.clear()


class SQLiteCache:
    """ファイルベースの永続キャッシュ。WAL モードで並行読み取りに強い。"""

    def __init__(self, path: str | Path, clock: Any = time.time) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS cache ("
            " key TEXT PRIMARY KEY, value TEXT NOT NULL, expires_at REAL)"
        )

    def get(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return None
            value, expires = row
            if expires is not None and expires <= self._clock():
                self._conn.execute("DELETE FROM cache WHERE key = ?", (key,))
                return None
            return str(value)

    def set(self, key: str, value: str, ttl: int | None = None) -> None:
        expires = self._clock() + ttl if ttl else None
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
                (key, value, expires),
            )

    def purge_expired(self) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM cache WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (self._clock(),),
            )
            return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class RedisCache:
    def __init__(self, client: Any, prefix: str = "corpnorm:") -> None:
        self._client = client
        self.prefix = prefix

    @classmethod
    def from_url(cls, url: str, prefix: str = "corpnorm:") -> RedisCache:
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - 依存有無に依存
            raise RuntimeError(
                "Redis キャッシュには redis パッケージが必要です: pip install 'corpnorm[redis]'"
            ) from exc
        return cls(redis.Redis.from_url(url, decode_responses=True), prefix)

    def get(self, key: str) -> str | None:
        try:
            value = self._client.get(self.prefix + key)
        except Exception as exc:  # 障害時はキャッシュミス扱い
            logger.warning("redis get failed: %s", exc)
            return None
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    def set(self, key: str, value: str, ttl: int | None = None) -> None:
        try:
            self._client.set(self.prefix + key, value, ex=ttl or None)
        except Exception as exc:
            logger.warning("redis set failed: %s", exc)

    @property
    def client(self) -> Any:
        return self._client

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._client.close()


def create_cache(url: str | None) -> Cache:
    """URL からキャッシュ実装を生成する。"""
    if not url or url in ("none", "none://", "off"):
        return NullCache()
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme == "memory":
        return MemoryCache()
    if scheme == "sqlite":
        # sqlite:///relative.db → relative.db, sqlite:////abs/path.db → /abs/path.db
        path = url[len("sqlite:///") :] if url.startswith("sqlite:///") else parsed.path
        if not path:
            raise ValueError(f"SQLite キャッシュのパスが指定されていません: {url}")
        return SQLiteCache(path)
    if scheme in ("redis", "rediss", "unix"):
        return RedisCache.from_url(url)
    raise ValueError(f"未対応のキャッシュ URL です: {url}（memory:// / sqlite:/// / redis://）")
