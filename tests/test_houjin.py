from collections.abc import Callable

import httpx
import pytest

from corpnorm.address import normalize_address
from corpnorm.cache import MemoryCache
from corpnorm.houjin import (
    CorporateMatcher,
    Corporation,
    HoujinAPIError,
    HoujinClient,
    corporate_number_check_digit,
    is_valid_corporate_number,
    parse_corporations_xml,
)
from corpnorm.issues import IssueCode
from corpnorm.name import normalize_name
from tests.conftest import CLOSED, YAMADA, YAMADA_TOKYO, FakeHoujin, corp_xml, make_number


def test_check_digit_known_numbers() -> None:
    # 公表されている法人番号（トヨタ自動車株式会社・国税庁）
    assert corporate_number_check_digit("180301018771") == 1
    assert is_valid_corporate_number("1180301018771")
    assert is_valid_corporate_number("7000012050002")
    assert is_valid_corporate_number("１１８０３０１０１８７７１")
    assert not is_valid_corporate_number("2180301018771")
    assert not is_valid_corporate_number("118030101877")
    assert not is_valid_corporate_number(None)
    with pytest.raises(ValueError):
        corporate_number_check_digit("123")


def test_parse_xml() -> None:
    corps = parse_corporations_xml(corp_xml(YAMADA, CLOSED))
    assert [c.name for c in corps] == ["株式会社山田商事", "株式会社旧山田"]
    assert corps[0].address == "大阪府大阪市北区梅田3丁目1-3"
    assert not corps[0].is_closed and corps[1].is_closed
    d = corps[0].to_dict()
    assert d["kind_label"] == "株式会社" and d["address"].startswith("大阪府")
    assert Corporation.from_dict({**d, "unknown": 1}).corporate_number == YAMADA["corporateNumber"]


def test_parse_xml_rejects_bad_payloads() -> None:
    with pytest.raises(HoujinAPIError):
        parse_corporations_xml("<not-xml")
    with pytest.raises(HoujinAPIError):
        parse_corporations_xml('<!DOCTYPE x [<!ENTITY a "b">]><corporations/>')
    # 必須項目が欠けたレコードは読み飛ばす
    assert parse_corporations_xml("<corporations><corporation/></corporations>") == []
    assert parse_corporations_xml(b"<corporations></corporations>") == []


def test_get_by_number(make_client: Callable[..., HoujinClient], fake_houjin: FakeHoujin) -> None:
    client = make_client()
    corp = client.get_by_number(YAMADA["corporateNumber"])
    assert corp is not None and corp.name == "株式会社山田商事"
    req = fake_houjin.requests[-1]
    assert req.url.params["id"] == "test-app-id"
    assert req.url.params["type"] == "12"
    assert client.get_by_number(make_number("999999999999")) is None
    with pytest.raises(ValueError):
        client.get_by_number("123")
    client.close()


def test_search_params(make_client: Callable[..., HoujinClient], fake_houjin: FakeHoujin) -> None:
    client = make_client()
    assert client.search_by_name("  ") == []
    found = client.search_by_name("山田商事", prefecture_code="27")
    assert [c.prefecture_name for c in found] == ["大阪府"]
    q = fake_houjin.requests[-1].url.params
    assert (q["mode"], q["target"], q["close"], q["address"]) == ("2", "1", "0", "27")


def test_cache_prevents_repeat_calls(
    make_client: Callable[..., HoujinClient], fake_houjin: FakeHoujin
) -> None:
    client = make_client(cache=MemoryCache())
    client.search_by_name("山田商事")
    client.search_by_name("山田商事")
    client.search_by_name("存在しない")  # ネガティブキャッシュ
    client.search_by_name("存在しない")
    assert len(fake_houjin.requests) == 2
    assert client.cache_stats.to_dict() == {"hits": 2, "misses": 2}
    assert client.api_calls == 2


def test_retry_then_success(
    make_client: Callable[..., HoujinClient], fake_houjin: FakeHoujin
) -> None:
    sleeps: list[float] = []
    fake_houjin.fail_times = 2
    client = make_client(sleep=sleeps.append, backoff_base=0.5)
    assert client.search_by_name("山田商事")
    assert sleeps == [0.5, 1.0]  # 指数バックオフ


def test_retry_exhausted(make_client: Callable[..., HoujinClient], fake_houjin: FakeHoujin) -> None:
    fake_houjin.fail_times = 10
    client = make_client(max_retries=2)
    with pytest.raises(HoujinAPIError):
        client.search_by_name("山田商事")
    assert len(fake_houjin.requests) == 3


def test_non_retryable_status(
    make_client: Callable[..., HoujinClient], fake_houjin: FakeHoujin
) -> None:
    fake_houjin.fail_times = 1
    fake_houjin.fail_status = 403
    client = make_client()
    with pytest.raises(HoujinAPIError) as ei:
        client.search_by_name("山田商事")
    assert ei.value.status_code == 403
    assert len(fake_houjin.requests) == 1


def test_transport_error_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, text=corp_xml(YAMADA))

    with HoujinClient(
        "id", http_client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None
    ) as client:
        assert client.search_by_name("山田")[0].name == "株式会社山田商事"


def test_rate_limiter_called(make_client: Callable[..., HoujinClient]) -> None:
    class Counter:
        n = 0

        def acquire(self) -> None:
            self.n += 1

    rl = Counter()
    make_client(rate_limiter=rl).search_by_name("山田商事")
    assert rl.n == 1


def test_client_requires_app_id() -> None:
    with pytest.raises(ValueError):
        HoujinClient("")
    HoujinClient("x").close()


# ---------------------------------------------------------------- matcher


def test_match_uses_address_to_disambiguate(matcher: CorporateMatcher) -> None:
    r = matcher.match(
        normalize_name("(株)山田商事"), normalize_address("大阪市北区梅田三丁目1番3号")
    )
    assert r.status == "matched"
    assert r.corporation is not None
    assert r.corporation.corporate_number == YAMADA["corporateNumber"]
    assert r.score is not None and r.score >= 0.85


def test_match_tokyo_suffix(matcher: CorporateMatcher) -> None:
    r = matcher.match(normalize_name("山田商事㈱"), normalize_address("千代田区丸の内1-1-1"))
    assert r.status == "matched"
    assert r.corporation is not None
    assert r.corporation.corporate_number == YAMADA_TOKYO["corporateNumber"]


def test_match_ambiguous_without_address(matcher: CorporateMatcher) -> None:
    r = matcher.match(normalize_name("山田商事"))
    assert r.status == "ambiguous"
    assert len(r.candidates) == 2
    assert r.issues[0].code is IssueCode.CORPORATION_AMBIGUOUS
    assert r.to_dict()["candidates"][0]["score"] > 0


def test_match_name_changed_issue(matcher: CorporateMatcher) -> None:
    r = matcher.match(
        normalize_name("株式会社山田商事"), normalize_address("大阪府大阪市北区梅田3-1-3")
    )
    assert r.status == "matched"
    assert all(i.code is not IssueCode.CORPORATE_NAME_CHANGED for i in r.issues)


def test_match_falls_back_to_nationwide(matcher: CorporateMatcher, fake_houjin: FakeHoujin) -> None:
    # 住所は福岡だが、法人は大阪にしかない → 都道府県条件を外して再検索
    r = matcher.match(
        normalize_name("株式会社山田商事"), normalize_address("福岡県福岡市中央区天神1-1")
    )
    assert len(fake_houjin.requests) == 2
    assert r.status in {"matched", "ambiguous"}


def test_match_not_found(matcher: CorporateMatcher) -> None:
    r = matcher.match(normalize_name("存在しない商会"))
    assert r.status == "not_found"
    assert r.issues[0].code is IssueCode.CORPORATION_NOT_FOUND
    assert matcher.match(normalize_name("")).status == "skipped"


def test_lookup_number(matcher: CorporateMatcher) -> None:
    ok = matcher.lookup_number(YAMADA["corporateNumber"])
    assert ok.status == "matched" and ok.score == 1.0
    closed = matcher.lookup_number(CLOSED["corporateNumber"])
    assert closed.issues[0].code is IssueCode.CORPORATION_CLOSED
    assert "承継法人" in closed.issues[0].message
    missing = matcher.lookup_number(make_number("999999999999"))
    assert missing.status == "not_found"
    assert missing.issues[0].code is IssueCode.CORPORATE_NUMBER_NOT_FOUND
    bad = matcher.lookup_number("1234567890123")
    assert bad.status == "error" and bad.issues[0].code is IssueCode.CORPORATE_NUMBER_INVALID


def test_api_failure_becomes_issue(matcher: CorporateMatcher, fake_houjin: FakeHoujin) -> None:
    fake_houjin.fail_times = 100
    r = matcher.match(normalize_name("山田商事"))
    assert r.status == "error"
    assert r.issues[0].code is IssueCode.ENRICHMENT_FAILED
    r = matcher.lookup_number(YAMADA["corporateNumber"])
    assert r.status == "error"


def test_closed_candidate_is_penalized(make_client: Callable[..., HoujinClient]) -> None:
    m = CorporateMatcher(make_client())
    c_open = m._score(normalize_name("株式会社山田"), None, Corporation("1", "株式会社山田"))
    c_closed = m._score(
        normalize_name("株式会社山田"),
        None,
        Corporation("2", "株式会社山田", close_date="2020-01-01"),
    )
    c_form = m._score(normalize_name("株式会社山田"), None, Corporation("3", "有限会社山田"))
    c_pos = m._score(normalize_name("株式会社山田"), None, Corporation("4", "山田株式会社"))
    assert c_open.score > c_pos.score > c_form.score
    assert c_open.score > c_closed.score


def test_bank_kana_matches_furigana(make_client: Callable[..., HoujinClient]) -> None:
    m = CorporateMatcher(make_client())
    corp = Corporation("1", "株式会社山田商事", furigana="ヤマダショウジ")
    kana = m._score(normalize_name("ｶ)ﾔﾏﾀﾞｼｮｳｼﾞ"), None, corp)
    assert kana.name_score == 1.0
    no_furigana = m._score(normalize_name("ｶ)ﾔﾏﾀﾞｼｮｳｼﾞ"), None, Corporation("2", "株式会社山田商事"))
    assert no_furigana.name_score < 0.5
