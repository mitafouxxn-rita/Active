from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from corpnorm.api.app import create_app
from corpnorm.api.settings import Settings
from corpnorm.houjin import CorporateMatcher
from corpnorm.postal import PostalDictionary
from tests.conftest import YAMADA, FakeHoujin, make_number


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Settings()))


@pytest.fixture
def enriched(matcher: CorporateMatcher) -> TestClient:
    return TestClient(create_app(Settings(max_batch_size=5), matcher=matcher))


def test_health(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert r.headers["X-Request-ID"]
    r = client.get("/readyz", headers={"X-Request-ID": "abc"})
    assert r.json() == {
        "status": "ok",
        "enrichment_available": False,
        "postal_dictionary_entries": 0,
    }
    assert r.headers["X-Request-ID"] == "abc"


def test_issue_codes(client: TestClient) -> None:
    codes = {c["code"]: c["severity"] for c in client.get("/v1/meta/issue-codes").json()}
    assert codes["EMPTY_NAME"] == "error" and codes["NO_LEGAL_FORM"] == "info"


def test_normalize_name(client: TestClient) -> None:
    r = client.post("/v1/normalize/name", json={"name": "ｶ)ﾔﾏﾀﾞｼｮｳｼﾞ"})
    assert r.status_code == 200
    body = r.json()
    assert body["normalized"] == "株式会社ヤマダショウジ"
    assert body["legal_form_position"] == "prefix"
    assert body["issues"][0]["code"] == "BANK_KANA_FORMAT"


def test_normalize_address(client: TestClient) -> None:
    r = client.post(
        "/v1/normalize/address",
        json={"address": "千代田区霞が関三丁目1番1号", "postal_code": "100-8978"},
    )
    assert r.status_code == 200
    assert r.json()["normalized"] == "東京都千代田区霞が関3-1-1"
    assert r.json()["prefecture_code"] == "13"


def test_validation_error_format(client: TestClient) -> None:
    r = client.post("/v1/normalize/name", json={})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "VALIDATION_ERROR" and err["details"] and err["request_id"]
    r = client.post("/v1/normalize/name", json={"name": "x" * 501})
    assert r.status_code == 422


def test_records_normalize_and_dedupe(client: TestClient) -> None:
    body = {
        "records": [
            {"id": "C-1", "name": "（株）山田商事", "address": "大阪市北区梅田1-1-1"},
            {
                "id": "C-2",
                "name": "株式会社山田商事",
                "address": "大阪府大阪市北区梅田一丁目1番1号",
            },
            {
                "id": "C-3",
                "name": "",
                "address": "東京都港区芝公園4-2-8",
                "postal_code": "530-0001",
            },
        ]
    }
    r = client.post("/v1/records/normalize", json=body)
    assert r.status_code == 200
    data = r.json()
    res = data["results"]
    assert res[0]["name"]["normalized"] == "株式会社山田商事"
    assert res[1]["duplicate"] == {"of_index": 0, "of_id": "C-1", "score": 1.0, "kind": "exact"}
    assert res[2]["health_score"] < 50
    assert data["summary"]["total"] == 3
    assert data["summary"]["duplicates"] == 1
    assert data["summary"]["with_errors"] == 1
    assert res[0]["enrichment"] is None


def test_records_without_dedupe(client: TestClient) -> None:
    body = {"records": [{"name": "A"}, {"name": "A"}], "options": {"dedupe": False}}
    res = client.post("/v1/records/normalize", json=body).json()["results"]
    assert res[1]["duplicate"] is None and res[1]["address"] is None


def test_enrich_unavailable(client: TestClient) -> None:
    r = client.post(
        "/v1/records/normalize", json={"records": [{"name": "A"}], "options": {"enrich": True}}
    )
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "ENRICHMENT_UNAVAILABLE"
    r = client.get(f"/v1/corporations/{YAMADA['corporateNumber']}")
    assert r.status_code == 503


def test_records_enrichment(enriched: TestClient) -> None:
    body = {
        "records": [
            {"name": "(株)山田商事", "address": "大阪市北区梅田3-1-3"},
            {"name": "山田商事"},
            {"name": "x", "corporate_number": YAMADA["corporateNumber"]},
        ],
        "options": {"enrich": True},
    }
    r = enriched.post("/v1/records/normalize", json=body)
    assert r.status_code == 200
    res = r.json()["results"]
    assert res[0]["enrichment"]["status"] == "matched"
    assert res[0]["corporate_number"] == YAMADA["corporateNumber"]
    assert res[0]["enrichment"]["corporation"]["address"].startswith("大阪府")
    assert res[1]["enrichment"]["status"] == "ambiguous"
    assert res[2]["duplicate"]["kind"] == "corporate_number"
    assert r.json()["summary"]["enrichment"] == {"matched": 2, "ambiguous": 1}


def test_batch_limit(enriched: TestClient) -> None:
    r = enriched.post("/v1/records/normalize", json={"records": [{"name": "A"}] * 6})
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "BATCH_TOO_LARGE"
    r = enriched.post("/v1/records/normalize", json={"records": []})
    assert r.status_code == 422


def test_get_corporation(enriched: TestClient, fake_houjin: FakeHoujin) -> None:
    r = enriched.get(f"/v1/corporations/{YAMADA['corporateNumber']}")
    assert r.status_code == 200 and r.json()["name"] == "株式会社山田商事"
    assert enriched.get("/v1/corporations/1234567890123").status_code == 400
    assert enriched.get("/v1/corporations/123").status_code == 422
    r = enriched.get(f"/v1/corporations/{make_number('999999999999')}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    fake_houjin.fail_times = 100
    r = enriched.get(f"/v1/corporations/{make_number('999999999998')}")
    assert r.status_code == 502


def test_api_key_auth() -> None:
    c = TestClient(create_app(Settings(api_keys=("secret-1", "secret-2"))))
    body = {"name": "(株)A"}
    assert c.post("/v1/normalize/name", json=body).status_code == 401
    r = c.post("/v1/normalize/name", json=body, headers={"X-API-Key": "wrong"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED"
    assert (
        c.post("/v1/normalize/name", json=body, headers={"X-API-Key": "secret-2"}).status_code
        == 200
    )
    assert c.get("/healthz").status_code == 200  # 死活監視は認証不要


def test_not_found_route(client: TestClient) -> None:
    r = client.get("/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "HTTP_404"


def test_settings_from_env(tmp_path: Path) -> None:
    s = Settings.from_env(
        {
            "CORPNORM_API_KEYS": "a, b,",
            "HOUJIN_APP_ID": "app",
            "CORPNORM_MAX_BATCH": "50",
            "CORPNORM_RATE_LIMIT": "2.5",
            "CORPNORM_CORS_ORIGINS": "https://example.com",
        }
    )
    assert s.api_keys == ("a", "b") and s.houjin_app_id == "app"
    assert s.max_batch_size == 50 and s.rate_limit == 2.5
    assert s.cors_origins == ("https://example.com",)


def test_app_builds_matcher_and_dictionary_from_settings(tmp_path: Path) -> None:
    fix = Path(__file__).parent / "fixtures" / "ken_all_sample.csv"
    app = create_app(
        Settings(
            houjin_app_id="app",
            cache_url=f"sqlite:///{tmp_path}/c.db",
            postal_dict_path=str(fix),
            cors_origins=("https://example.com",),
        )
    )
    with TestClient(app) as c:
        ready = c.get("/readyz").json()
        assert ready["enrichment_available"] is True
        assert ready["postal_dictionary_entries"] == 10
        r = c.options(
            "/v1/normalize/name",
            headers={"Origin": "https://example.com", "Access-Control-Request-Method": "POST"},
        )
        assert r.headers["access-control-allow-origin"] == "https://example.com"


def test_app_with_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    import fakeredis

    monkeypatch.setattr(
        "redis.Redis.from_url", lambda url, **kw: fakeredis.FakeRedis(decode_responses=True)
    )
    app = create_app(Settings(houjin_app_id="app", cache_url="redis://localhost/0"))
    with TestClient(app) as c:
        assert c.get("/readyz").json()["enrichment_available"] is True


def test_postal_dict_injection() -> None:
    fix = Path(__file__).parent / "fixtures" / "ken_all_sample.csv"
    c = TestClient(create_app(Settings(), postal_dict=PostalDictionary.load(fix)))
    r = c.post(
        "/v1/normalize/address", json={"address": "東京都港区芝公園4-2-8", "postal_code": "1008978"}
    )
    assert "POSTAL_CITY_MISMATCH" in {i["code"] for i in r.json()["issues"]}


def test_openapi_document(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    assert spec["info"]["title"] == "corpnorm API"
    for path in ("/v1/normalize/name", "/v1/normalize/address", "/v1/records/normalize",
                 "/v1/corporations/{corporate_number}"):  # fmt: skip
        assert path in spec["paths"]
    assert "413" in spec["paths"]["/v1/records/normalize"]["post"]["responses"]
