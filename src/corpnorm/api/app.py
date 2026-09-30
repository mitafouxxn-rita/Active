"""FastAPI アプリケーション。

    uvicorn corpnorm.api.app:create_app --factory --host 0.0.0.0 --port 8000

Swagger UI は ``/docs``、OpenAPI 定義は ``/openapi.json``。
"""

from __future__ import annotations

import logging
import secrets
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from corpnorm import __version__
from corpnorm.address import normalize_address
from corpnorm.api import schemas as s
from corpnorm.api.settings import Settings
from corpnorm.dedupe import DuplicateDetector
from corpnorm.houjin import CorporateMatcher, is_valid_corporate_number
from corpnorm.issues import DEFAULT_SEVERITY, Severity
from corpnorm.name import normalize_name
from corpnorm.pipeline import InputRecord, RecordProcessor, dedupe_record
from corpnorm.postal import PostalDictionary

logger = logging.getLogger("corpnorm.api")

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": s.ErrorResponse, "description": "API キーが無効"},
    422: {"model": s.ErrorResponse, "description": "入力値の検証エラー"},
}

_DESCRIPTION = """
日本の法人名・住所データを正規化し、国税庁 法人番号公表サイトの情報で名寄せ・補完する API です。

* **法人名**: `(株)` `㈱` `ｶ)` などの表記揺れを統一し、前株／後株を判定
* **住所**: 漢数字・全角数字・丁目番地号を `1-2-3` 形式に統一、都道府県を補完、郵便番号と照合
* **名寄せ**: 法人番号 API で正式商号・13 桁の法人番号・最新の本店所在地を特定
* **品質**: 検出事項コードとデータ健全性スコア、リクエスト内の重複候補を返却

認証: `CORPNORM_API_KEYS` を設定した場合、`X-API-Key` ヘッダーが必須になります。
"""


def _build_matcher(settings: Settings) -> CorporateMatcher | None:
    if not settings.houjin_app_id:
        return None
    from corpnorm.cache import RedisCache, create_cache
    from corpnorm.houjin import DEFAULT_BASE_URL, HoujinClient
    from corpnorm.ratelimit import RateLimiter, RedisFixedWindowLimiter, TokenBucket

    cache = create_cache(settings.cache_url)
    limiter: RateLimiter
    if isinstance(cache, RedisCache):
        # 複数ワーカー・複数コンテナで国税庁 API の呼び出し上限を共有する
        limiter = RedisFixedWindowLimiter(cache.client, max(1, int(settings.rate_limit)))
    else:
        limiter = TokenBucket(settings.rate_limit)
    client = HoujinClient(
        settings.houjin_app_id,
        base_url=settings.houjin_base_url or DEFAULT_BASE_URL,
        cache=cache,
        rate_limiter=limiter,
        cache_ttl=settings.cache_ttl,
    )
    return CorporateMatcher(client)


def create_app(
    settings: Settings | None = None,
    *,
    matcher: CorporateMatcher | None = None,
    postal_dict: PostalDictionary | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    if matcher is None:
        matcher = _build_matcher(settings)
    if postal_dict is None and settings.postal_dict_path:
        postal_dict = PostalDictionary.load(settings.postal_dict_path)

    normalizer = RecordProcessor(postal_dict=postal_dict)
    enricher = RecordProcessor(matcher=matcher, postal_dict=postal_dict) if matcher else None
    pool = ThreadPoolExecutor(max_workers=max(1, settings.enrich_workers))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> Any:
        yield
        pool.shutdown(wait=False)
        if matcher is not None:
            matcher.client.close()
            matcher.client.cache.close()

    app = FastAPI(
        title="corpnorm API",
        version=__version__,
        description=_DESCRIPTION.strip(),
        lifespan=lifespan,
        openapi_tags=[
            {"name": "normalize", "description": "法人名・住所の正規化"},
            {"name": "records", "description": "レコード一括処理（正規化・名寄せ・重複判定）"},
            {"name": "corporations", "description": "法人番号 API 連携"},
            {"name": "meta", "description": "稼働状況・メタ情報"},
        ],
    )
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
        )

    # ------------------------------------------------------------ middleware / errors

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Response:
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = rid
        t0 = time.perf_counter()
        response: Response = await call_next(request)
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        response.headers["X-Request-ID"] = rid
        logger.info(
            "%s %s %d %.1fms",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            extra={"request_id": rid, "status": response.status_code, "elapsed_ms": elapsed_ms},
        )
        return response

    def _error(request: Request, status: int, code: str, message: str, **kw: Any) -> JSONResponse:
        body = {
            "error": {
                "code": code,
                "message": message,
                "request_id": getattr(request.state, "request_id", None),
                **kw,
            }
        }
        return JSONResponse(status_code=status, content=body)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return _error(
            request,
            exc.status_code,
            detail.get("code", f"HTTP_{exc.status_code}"),
            detail.get("message", str(exc.detail)),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")}
            for e in exc.errors()
        ]
        return _error(request, 422, "VALIDATION_ERROR", "入力値が不正です", details=details)

    def fail(status: int, code: str, message: str) -> HTTPException:
        return HTTPException(status_code=status, detail={"code": code, "message": message})

    def require_api_key(
        x_api_key: Annotated[str | None, Header(description="API キー")] = None,
    ) -> None:
        if not settings.api_keys:
            return
        if not x_api_key or not any(
            secrets.compare_digest(x_api_key, k) for k in settings.api_keys
        ):
            raise fail(401, "UNAUTHORIZED", "X-API-Key が無効です")

    auth = [Depends(require_api_key)]

    # ------------------------------------------------------------ meta

    @app.get("/healthz", response_model=s.HealthResponse, tags=["meta"], summary="死活監視")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz", response_model=s.ReadinessResponse, tags=["meta"], summary="準備状況")
    def readyz() -> dict[str, Any]:
        return {
            "status": "ok",
            "enrichment_available": matcher is not None,
            "postal_dictionary_entries": len(postal_dict) if postal_dict else 0,
        }

    @app.get(
        "/v1/meta/issue-codes",
        response_model=list[s.IssueCodeInfo],
        tags=["meta"],
        summary="検出事項コードの一覧",
    )
    def issue_codes() -> list[dict[str, Any]]:
        return [{"code": c, "severity": sev} for c, sev in DEFAULT_SEVERITY.items()]

    # ------------------------------------------------------------ normalize

    @app.post(
        "/v1/normalize/name",
        response_model=s.NameResult,
        tags=["normalize"],
        summary="法人名を正規化",
        dependencies=auth,
        responses=_ERRORS,
    )
    def post_name(body: s.NameRequest) -> dict[str, Any]:
        return normalize_name(body.name).to_dict()

    @app.post(
        "/v1/normalize/address",
        response_model=s.AddressResult,
        tags=["normalize"],
        summary="住所を正規化し郵便番号と照合",
        dependencies=auth,
        responses=_ERRORS,
    )
    def post_address(body: s.AddressRequest) -> dict[str, Any]:
        return normalize_address(body.address, body.postal_code, postal_dict=postal_dict).to_dict()

    # ------------------------------------------------------------ records

    @app.post(
        "/v1/records/normalize",
        response_model=s.BatchResponse,
        tags=["records"],
        summary="レコードを一括で正規化・名寄せ・重複判定",
        description=(
            f"1 リクエストあたり最大 {settings.max_batch_size} 件。"
            "`options.enrich=true` の場合は法人番号 API で名寄せします"
            "（サーバーに HOUJIN_APP_ID が設定されていない場合は 503）。"
        ),
        dependencies=auth,
        responses={
            **_ERRORS,
            413: {"model": s.ErrorResponse, "description": "件数が上限を超過"},
            503: {"model": s.ErrorResponse, "description": "名寄せ機能が無効"},
        },
    )
    def post_records(body: s.BatchRequest) -> dict[str, Any]:
        if len(body.records) > settings.max_batch_size:
            raise fail(
                413,
                "BATCH_TOO_LARGE",
                f"1 リクエストの上限は {settings.max_batch_size} 件です（{len(body.records)} 件）",
            )
        processor = normalizer
        if body.options.enrich:
            if enricher is None:
                raise fail(503, "ENRICHMENT_UNAVAILABLE", "法人番号 API 連携が設定されていません")
            processor = enricher
        inputs = [
            InputRecord(r.name, r.address, r.postal_code, r.corporate_number, r.id)
            for r in body.records
        ]
        if processor is enricher and len(inputs) > 1:
            processed = list(pool.map(processor.process, inputs))
        else:
            processed = [processor.process(r) for r in inputs]

        detector = (
            DuplicateDetector(body.options.duplicate_threshold) if body.options.dedupe else None
        )
        results: list[dict[str, Any]] = []
        enrich_counter: Counter[str] = Counter()
        with_errors = with_warnings = dups = score_sum = 0
        for idx, rec in enumerate(processed):
            if detector is not None:
                dedupe_record(detector, idx, rec)
            d = rec.to_dict()
            dup = d.pop("duplicate")
            if dup is not None:
                dups += 1
                dup = {
                    "of_index": dup["of_row"],
                    "of_id": dup["of_id"],
                    "score": dup["score"],
                    "kind": dup["kind"],
                }
            severities = {i.severity for i in rec.issues}
            with_errors += Severity.ERROR in severities
            with_warnings += Severity.WARNING in severities
            score_sum += rec.health_score
            if rec.enrichment:
                enrich_counter[rec.enrichment.status] += 1
            results.append({"index": idx, **d, "duplicate": dup})
        return {
            "results": results,
            "summary": {
                "total": len(results),
                "with_errors": with_errors,
                "with_warnings": with_warnings,
                "duplicates": dups,
                "average_health_score": round(score_sum / len(results), 2),
                "enrichment": dict(enrich_counter),
            },
        }

    # ------------------------------------------------------------ corporations

    @app.get(
        "/v1/corporations/{corporate_number}",
        response_model=s.CorporationModel,
        tags=["corporations"],
        summary="法人番号から最新の法人情報を取得",
        dependencies=auth,
        responses={
            **_ERRORS,
            400: {"model": s.ErrorResponse, "description": "法人番号が不正"},
            404: {"model": s.ErrorResponse, "description": "該当なし"},
            502: {"model": s.ErrorResponse, "description": "国税庁 API のエラー"},
            503: {"model": s.ErrorResponse, "description": "名寄せ機能が無効"},
        },
    )
    def get_corporation(
        corporate_number: Annotated[str, Path(pattern=r"^\d{13}$", description="13 桁の法人番号")],
    ) -> dict[str, Any]:
        if matcher is None:
            raise fail(503, "ENRICHMENT_UNAVAILABLE", "法人番号 API 連携が設定されていません")
        if not is_valid_corporate_number(corporate_number):
            raise fail(400, "INVALID_CORPORATE_NUMBER", "法人番号のチェックデジットが一致しません")
        result = matcher.lookup_number(corporate_number)
        if result.status == "error":
            raise fail(502, "UPSTREAM_ERROR", "国税庁 API の照会に失敗しました")
        if result.corporation is None:
            raise fail(404, "NOT_FOUND", f"法人番号 {corporate_number} は見つかりません")
        return result.corporation.to_dict()

    return app
