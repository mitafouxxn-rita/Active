"""国税庁 法人番号公表サイト Web-API（Ver.4）クライアントと名寄せ（マッチング）。

* 法人番号指定検索: ``GET /4/num?id=...&number=...&type=12``
* 法人名検索:       ``GET /4/name?id=...&name=...&type=12&mode=2&target=1&address=13``

レスポンスは XML（type=12, Unicode）で受け取る。アプリケーション ID は国税庁への
利用届出で発行されるものを ``HOUJIN_APP_ID`` 環境変数などで渡す。

呼び出しは必ず キャッシュ → レート制限 → HTTP（指数バックオフ付き再試行）の順に通る。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from corpnorm.address import NormalizedAddress, normalize_address
from corpnorm.cache import Cache, CacheStats, NullCache
from corpnorm.issues import Issue, IssueCode
from corpnorm.name import NormalizedName, normalize_name
from corpnorm.ratelimit import NoRateLimit, RateLimiter
from corpnorm.similarity import similarity
from corpnorm.text import is_katakana_only, match_key

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.houjin-bangou.nta.go.jp/4"
_RETRY_STATUS = {429, 500, 502, 503, 504}

KIND_LABELS = {
    "101": "国の機関",
    "201": "地方公共団体",
    "301": "株式会社",
    "302": "有限会社",
    "303": "合名会社",
    "304": "合資会社",
    "305": "合同会社",
    "399": "その他の設立登記法人",
    "401": "外国会社等",
    "499": "その他",
}


# ---------------------------------------------------------------- 法人番号


def corporate_number_check_digit(base12: str) -> int:
    """12 桁の基礎番号からチェックデジットを計算する（法人番号の指定を行う省令 第2条）。"""
    if len(base12) != 12 or not base12.isdigit():
        raise ValueError("基礎番号は 12 桁の数字です")
    total = 0
    for n, ch in enumerate(reversed(base12), start=1):
        total += int(ch) * (1 if n % 2 else 2)
    return 9 - (total % 9)


def normalize_corporate_number(value: str | None) -> str | None:
    if value is None:
        return None
    from corpnorm.text import normalize_width

    digits = re.sub(r"[^0-9]", "", normalize_width(value))
    return digits or None


def is_valid_corporate_number(value: str | None) -> bool:
    n = normalize_corporate_number(value)
    if n is None or len(n) != 13:
        return False
    return int(n[0]) == corporate_number_check_digit(n[1:])


# ---------------------------------------------------------------- データ型


@dataclass(frozen=True)
class Corporation:
    corporate_number: str
    name: str
    kind: str | None = None
    furigana: str | None = None
    en_name: str | None = None
    prefecture_name: str | None = None
    city_name: str | None = None
    street_number: str | None = None
    post_code: str | None = None
    close_date: str | None = None
    close_cause: str | None = None
    successor_corporate_number: str | None = None
    change_date: str | None = None
    update_date: str | None = None
    latest: bool = True

    @property
    def address(self) -> str:
        return "".join(p for p in (self.prefecture_name, self.city_name, self.street_number) if p)

    @property
    def is_closed(self) -> bool:
        return bool(self.close_date)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["address"] = self.address
        d["kind_label"] = KIND_LABELS.get(self.kind or "")
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Corporation:
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})


_XML_FIELDS = {
    "corporateNumber": "corporate_number",
    "name": "name",
    "kind": "kind",
    "furigana": "furigana",
    "enName": "en_name",
    "prefectureName": "prefecture_name",
    "cityName": "city_name",
    "streetNumber": "street_number",
    "postCode": "post_code",
    "closeDate": "close_date",
    "closeCause": "close_cause",
    "successorCorporateNumber": "successor_corporate_number",
    "changeDate": "change_date",
    "updateDate": "update_date",
    "latest": "latest",
}


class HoujinAPIError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def parse_corporations_xml(payload: str | bytes) -> list[Corporation]:
    """``<corporations>`` XML を :class:`Corporation` のリストに変換する。"""
    text = payload if isinstance(payload, str) else payload.decode("utf-8", "replace")
    # 実体参照の展開（XXE / billion laughs）を避けるため DOCTYPE 付き XML は拒否する
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise HoujinAPIError("DOCTYPE / ENTITY 宣言を含む XML は受け付けません")
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as exc:
        raise HoujinAPIError(f"XML の解析に失敗しました: {exc}") from exc
    result: list[Corporation] = []
    for node in root.iter("corporation"):
        values: dict[str, Any] = {}
        for tag, attr in _XML_FIELDS.items():
            el = node.find(tag)
            text = (el.text or "").strip() if el is not None else ""
            values[attr] = text or None
        if not values.get("corporate_number") or not values.get("name"):
            continue
        values["latest"] = values.get("latest") != "0"
        result.append(Corporation(**values))
    return result


# ---------------------------------------------------------------- HTTP クライアント


class HoujinClient:
    def __init__(
        self,
        app_id: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        cache: Cache | None = None,
        rate_limiter: RateLimiter | None = None,
        http_client: httpx.Client | None = None,
        timeout: float = 10.0,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        cache_ttl: int = 7 * 24 * 3600,
        negative_cache_ttl: int = 24 * 3600,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not app_id:
            raise ValueError("国税庁 API のアプリケーション ID が必要です（HOUJIN_APP_ID）")
        self.app_id = app_id
        self.base_url = base_url.rstrip("/")
        self.cache: Cache = cache if cache is not None else NullCache()
        self.rate_limiter: RateLimiter = rate_limiter if rate_limiter is not None else NoRateLimit()
        self._http = http_client or httpx.Client(
            timeout=timeout, headers={"User-Agent": "corpnorm/1.0"}
        )
        self._owns_http = http_client is None
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.cache_ttl = cache_ttl
        self.negative_cache_ttl = negative_cache_ttl
        self._sleep = sleep
        self.cache_stats = CacheStats()
        self.api_calls = 0

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> HoujinClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- 公開 API

    def get_by_number(self, number: str, *, history: bool = False) -> Corporation | None:
        n = normalize_corporate_number(number)
        if n is None or not is_valid_corporate_number(n):
            raise ValueError(f"法人番号が不正です: {number!r}")
        params = {"number": n, "type": "12", "history": "1" if history else "0"}
        found = self._request("num", params)
        latest = [c for c in found if c.latest] or found
        return latest[0] if latest else None

    def search_by_name(
        self,
        name: str,
        *,
        prefecture_code: str | None = None,
        mode: int = 2,
        target: int = 1,
        include_closed: bool = False,
    ) -> list[Corporation]:
        """法人名検索。``mode`` 1=前方一致 / 2=部分一致、``target`` 1=あいまい / 2=完全一致。"""
        if not name.strip():
            return []
        params: dict[str, str] = {
            "name": name,
            "type": "12",
            "mode": str(mode),
            "target": str(target),
            "close": "1" if include_closed else "0",
        }
        if prefecture_code:
            params["address"] = prefecture_code
        return self._request("name", params)

    # -- 内部

    def _cache_key(self, endpoint: str, params: dict[str, str]) -> str:
        raw = json.dumps([endpoint, sorted(params.items())], ensure_ascii=False)
        return f"houjin:v4:{endpoint}:{hashlib.sha256(raw.encode()).hexdigest()[:32]}"

    def _request(self, endpoint: str, params: dict[str, str]) -> list[Corporation]:
        key = self._cache_key(endpoint, params)
        cached = self.cache.get(key)
        if cached is not None:
            self.cache_stats.record(True)
            return [Corporation.from_dict(d) for d in json.loads(cached)]
        self.cache_stats.record(False)

        payload = self._fetch(endpoint, params)
        corps = parse_corporations_xml(payload)
        ttl = self.cache_ttl if corps else self.negative_cache_ttl
        self.cache.set(key, json.dumps([asdict(c) for c in corps], ensure_ascii=False), ttl=ttl)
        return corps

    def _fetch(self, endpoint: str, params: dict[str, str]) -> bytes:
        url = f"{self.base_url}/{endpoint}"
        query = {"id": self.app_id, **params}
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self.rate_limiter.acquire()
            self.api_calls += 1
            try:
                resp = self._http.get(url, params=query)
            except httpx.TransportError as exc:
                last_error = exc
                logger.warning("houjin api transport error (attempt %d): %s", attempt + 1, exc)
            else:
                if resp.status_code == 200:
                    return bytes(resp.content)
                if resp.status_code not in _RETRY_STATUS:
                    raise HoujinAPIError(
                        f"国税庁 API がエラーを返しました: HTTP {resp.status_code}",
                        resp.status_code,
                    )
                last_error = HoujinAPIError(f"HTTP {resp.status_code}", resp.status_code)
                logger.warning("houjin api HTTP %d (attempt %d)", resp.status_code, attempt + 1)
            if attempt < self.max_retries:
                self._sleep(self.backoff_base * (2**attempt))
        raise HoujinAPIError(f"国税庁 API への接続に失敗しました: {last_error}")


# ---------------------------------------------------------------- マッチング


@dataclass(frozen=True)
class MatchCandidate:
    corporation: Corporation
    score: float
    name_score: float
    address_score: float | None


@dataclass(frozen=True)
class MatchResult:
    status: str  # matched | ambiguous | not_found | error | skipped
    corporation: Corporation | None = None
    score: float | None = None
    candidates: tuple[MatchCandidate, ...] = ()
    issues: tuple[Issue, ...] = field(default_factory=tuple)

    def to_dict(self, max_candidates: int = 3) -> dict[str, Any]:
        return {
            "status": self.status,
            "score": self.score,
            "corporation": self.corporation.to_dict() if self.corporation else None,
            "candidates": [
                {
                    "corporate_number": c.corporation.corporate_number,
                    "name": c.corporation.name,
                    "address": c.corporation.address,
                    "score": round(c.score, 4),
                }
                for c in self.candidates[:max_candidates]
            ],
            "issues": [i.to_dict() for i in self.issues],
        }


class CorporateMatcher:
    """正規化済みの法人名・住所から、法人番号公表サイト上の法人を特定する。"""

    def __init__(
        self,
        client: HoujinClient,
        *,
        accept_threshold: float = 0.85,
        min_margin: float = 0.05,
        name_weight: float = 0.7,
    ) -> None:
        self.client = client
        self.accept_threshold = accept_threshold
        self.min_margin = min_margin
        self.name_weight = name_weight

    def lookup_number(self, number: str) -> MatchResult:
        if not is_valid_corporate_number(number):
            return MatchResult(
                "error",
                issues=(
                    Issue.of(
                        IssueCode.CORPORATE_NUMBER_INVALID,
                        f"法人番号のチェックデジットが一致しません: {number}",
                        "corporate_number",
                    ),
                ),
            )
        try:
            corp = self.client.get_by_number(number)
        except HoujinAPIError as exc:
            return self._failed(exc)
        if corp is None:
            return MatchResult(
                "not_found",
                issues=(
                    Issue.of(
                        IssueCode.CORPORATE_NUMBER_NOT_FOUND,
                        f"法人番号 {number} は公表サイトに存在しません",
                        "corporate_number",
                    ),
                ),
            )
        return MatchResult("matched", corp, 1.0, issues=self._status_issues(corp, None))

    def match(self, name: NormalizedName, address: NormalizedAddress | None = None) -> MatchResult:
        if not name.core:
            return MatchResult("skipped")
        pref_code = address.prefecture_code if address else None
        try:
            found = self.client.search_by_name(name.core, prefecture_code=pref_code)
            if not found and pref_code:
                # 本店移転などで都道府県が異なる可能性があるため条件を緩めて再検索
                found = self.client.search_by_name(name.core)
        except HoujinAPIError as exc:
            return self._failed(exc)
        if not found:
            return MatchResult(
                "not_found",
                issues=(
                    Issue.of(
                        IssueCode.CORPORATION_NOT_FOUND,
                        f"「{name.core}」に該当する法人が見つかりません",
                        "name",
                    ),
                ),
            )

        ranked = sorted((self._score(name, address, c) for c in found), key=lambda m: -m.score)
        best = ranked[0]
        second = ranked[1].score if len(ranked) > 1 else 0.0
        if best.score >= self.accept_threshold and best.score - second >= self.min_margin:
            return MatchResult(
                "matched",
                best.corporation,
                round(best.score, 4),
                tuple(ranked),
                self._status_issues(best.corporation, name),
            )
        return MatchResult(
            "ambiguous",
            None,
            round(best.score, 4),
            tuple(ranked),
            (
                Issue.of(
                    IssueCode.CORPORATION_AMBIGUOUS,
                    f"候補 {len(ranked)} 件から一意に特定できません"
                    f"（最上位「{best.corporation.name}」スコア {best.score:.2f}）",
                    "name",
                ),
            ),
        )

    def _score(
        self, name: NormalizedName, address: NormalizedAddress | None, corp: Corporation
    ) -> MatchCandidate:
        cand_name = normalize_name(corp.name)
        name_score = similarity(name.match_key, cand_name.match_key)
        if corp.furigana and is_katakana_only(name.core):
            # 全銀カナ（ﾔﾏﾀﾞｼｮｳｼﾞ）は漢字の商号と比較できないため、フリガナと照合する
            name_score = max(name_score, similarity(name.match_key, match_key(corp.furigana)))
        if name.legal_form and cand_name.legal_form:
            if name.legal_form != cand_name.legal_form:
                name_score *= 0.85
            elif (
                name.legal_form_position
                and cand_name.legal_form_position
                and name.legal_form_position != cand_name.legal_form_position
            ):
                name_score *= 0.9
        if corp.is_closed:
            name_score *= 0.9

        addr_score: float | None = None
        if address is not None and address.prefecture:
            cand_addr = normalize_address(corp.address)
            addr_score = 0.0
            if cand_addr.prefecture == address.prefecture:
                addr_score += 0.4
                if address.city and cand_addr.city == address.city:
                    addr_score += 0.4
                    addr_score += 0.2 * similarity(
                        (address.town or "") + (address.block or ""),
                        (cand_addr.town or "") + (cand_addr.block or ""),
                    )
        if addr_score is None:
            total = name_score
        else:
            total = self.name_weight * name_score + (1 - self.name_weight) * addr_score
        return MatchCandidate(corp, total, name_score, addr_score)

    @staticmethod
    def _status_issues(corp: Corporation, name: NormalizedName | None) -> tuple[Issue, ...]:
        issues: list[Issue] = []
        if corp.is_closed:
            issues.append(
                Issue.of(
                    IssueCode.CORPORATION_CLOSED,
                    f"法人は閉鎖されています（{corp.close_date}）"
                    + (
                        f"。承継法人: {corp.successor_corporate_number}"
                        if corp.successor_corporate_number
                        else ""
                    ),
                    "corporate_number",
                )
            )
        if name is not None and normalize_name(corp.name).normalized != name.normalized:
            issues.append(
                Issue.of(
                    IssueCode.CORPORATE_NAME_CHANGED,
                    f"正式商号「{corp.name}」と入力値が異なります",
                    "name",
                )
            )
        return tuple(issues)

    @staticmethod
    def _failed(exc: Exception) -> MatchResult:
        logger.error("enrichment failed: %s", exc)
        return MatchResult(
            "error",
            issues=(
                Issue.of(IssueCode.ENRICHMENT_FAILED, f"法人番号 API の照会に失敗: {exc}", None),
            ),
        )
