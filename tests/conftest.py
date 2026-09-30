from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from corpnorm.houjin import CorporateMatcher, HoujinClient, corporate_number_check_digit


def make_number(base12: str) -> str:
    """12 桁の基礎番号からチェックデジット付き 13 桁の法人番号を作る。"""
    return f"{corporate_number_check_digit(base12)}{base12}"


def corp_xml(*corps: dict[str, str]) -> str:
    body = []
    for i, c in enumerate(corps, start=1):
        fields = "".join(f"<{k}>{v}</{k}>" for k, v in c.items())
        body.append(f"<corporation><sequenceNumber>{i}</sequenceNumber>{fields}</corporation>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?><corporations>'
        f"<lastUpdateDate>2026-09-01</lastUpdateDate><count>{len(corps)}</count>"
        f"<divideNumber>1</divideNumber><divideSize>1</divideSize>{''.join(body)}</corporations>"
    )


YAMADA = {
    "corporateNumber": make_number("120001000001"),
    "name": "株式会社山田商事",
    "kind": "301",
    "prefectureName": "大阪府",
    "cityName": "大阪市北区",
    "streetNumber": "梅田3丁目1-3",
    "postCode": "5300001",
    "latest": "1",
}
YAMADA_TOKYO = {
    "corporateNumber": make_number("010001000002"),
    "name": "山田商事株式会社",
    "kind": "301",
    "prefectureName": "東京都",
    "cityName": "千代田区",
    "streetNumber": "丸の内1丁目1-1",
    "postCode": "1000005",
    "latest": "1",
}
CLOSED = {
    "corporateNumber": make_number("120001000003"),
    "name": "株式会社旧山田",
    "kind": "301",
    "prefectureName": "大阪府",
    "cityName": "大阪市中央区",
    "streetNumber": "本町1-1",
    "closeDate": "2020-03-31",
    "closeCause": "21",
    "successorCorporateNumber": make_number("120001000001"),
    "latest": "1",
}


class FakeHoujin:
    """国税庁 API を模した httpx.MockTransport のハンドラ。"""

    def __init__(self) -> None:
        self.by_number: dict[str, dict[str, str]] = {}
        self.by_name: list[dict[str, str]] = []
        self.requests: list[httpx.Request] = []
        self.fail_times = 0
        self.fail_status = 503

    def add(self, *corps: dict[str, str]) -> FakeHoujin:
        for c in corps:
            self.by_number[c["corporateNumber"]] = c
            self.by_name.append(c)
        return self

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_times > 0:
            self.fail_times -= 1
            return httpx.Response(self.fail_status)
        q = request.url.params
        if request.url.path.endswith("/num"):
            c = self.by_number.get(q["number"])
            return httpx.Response(200, text=corp_xml(*([c] if c else [])))
        if request.url.path.endswith("/name"):
            name = q["name"]
            pref = q.get("address")
            from corpnorm.data import PREFECTURE_CODES

            hits = [
                c
                for c in self.by_name
                if name in c["name"]
                and (pref is None or PREFECTURE_CODES.get(c.get("prefectureName", "")) == pref)
                and (q.get("close") == "1" or "closeDate" not in c)
            ]
            return httpx.Response(200, text=corp_xml(*hits))
        return httpx.Response(404)


@pytest.fixture
def fake_houjin() -> FakeHoujin:
    return FakeHoujin().add(YAMADA, YAMADA_TOKYO, CLOSED)


@pytest.fixture
def make_client(fake_houjin: FakeHoujin) -> Callable[..., HoujinClient]:
    def factory(**kw: Any) -> HoujinClient:
        http = httpx.Client(transport=httpx.MockTransport(fake_houjin))
        kw.setdefault("sleep", lambda s: None)
        return HoujinClient("test-app-id", http_client=http, **kw)

    return factory


@pytest.fixture
def matcher(make_client: Callable[..., HoujinClient]) -> CorporateMatcher:
    return CorporateMatcher(make_client())
