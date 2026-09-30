"""API サーバー設定（環境変数から読み込む。12-Factor App 準拠）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _split(value: str | None) -> tuple[str, ...]:
    return tuple(v.strip() for v in (value or "").split(",") if v.strip())


@dataclass(frozen=True)
class Settings:
    api_keys: tuple[str, ...] = ()
    houjin_app_id: str | None = None
    houjin_base_url: str | None = None
    cache_url: str = "memory://"
    cache_ttl: int = 7 * 24 * 3600
    rate_limit: float = 5.0
    max_batch_size: int = 1000
    enrich_workers: int = 4
    postal_dict_path: str | None = None
    cors_origins: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        return cls(
            api_keys=_split(e.get("CORPNORM_API_KEYS")),
            houjin_app_id=e.get("HOUJIN_APP_ID") or None,
            houjin_base_url=e.get("HOUJIN_API_BASE_URL") or None,
            cache_url=e.get("CORPNORM_CACHE_URL", "memory://"),
            cache_ttl=int(e.get("CORPNORM_CACHE_TTL", str(7 * 24 * 3600))),
            rate_limit=float(e.get("CORPNORM_RATE_LIMIT", "5")),
            max_batch_size=int(e.get("CORPNORM_MAX_BATCH", "1000")),
            enrich_workers=int(e.get("CORPNORM_ENRICH_WORKERS", "4")),
            postal_dict_path=e.get("CORPNORM_POSTAL_DICT") or None,
            cors_origins=_split(e.get("CORPNORM_CORS_ORIGINS")),
        )
