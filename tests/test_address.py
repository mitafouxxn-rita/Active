import pytest

from corpnorm.address import (
    canonicalize_block,
    convert_kanji_numerals,
    kanji_to_int,
    normalize_address,
)
from corpnorm.issues import IssueCode


def codes(r):  # type: ignore[no-untyped-def]
    return {i.code for i in r.issues}


@pytest.mark.parametrize(
    ("kanji", "value"),
    [
        ("一", 1), ("十", 10), ("十一", 11), ("二十", 20), ("二十三", 23), ("九十九", 99),
        ("百", 100), ("百五", 105), ("三百二十一", 321), ("千", 1000), ("千二百三十四", 1234),
        ("一〇五", 105), ("二三", 23), ("〇", 0), ("壱", 1), ("弐拾", 20),
    ],
)  # fmt: skip
def test_kanji_to_int(kanji: str, value: int) -> None:
    assert kanji_to_int(kanji) == value


@pytest.mark.parametrize("bad", ["", "一a", "abc"])
def test_kanji_to_int_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        kanji_to_int(bad)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("霞が関三丁目一番一号", "霞が関3丁目1番1号"),
        ("二十三番地", "23番地"),
        ("銀座一-二-三", "銀座1-2-3"),
        # 地名中の漢数字は変換しない
        ("四日市市", "四日市市"),
        ("八王子市", "八王子市"),
        ("三番町五番地", "三番町5番地"),
        ("一番町", "一番町"),
        ("九段北一丁目", "九段北1丁目"),
        ("十日町市", "十日町市"),
        ("三条通", "三条通"),
        ("六本木六丁目十番一号", "六本木6丁目10番1号"),
        ("第五地割", "第5地割"),
    ],
)
def test_convert_kanji_numerals(raw: str, expected: str) -> None:
    assert convert_kanji_numerals(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1丁目2番3号", "1-2-3"),
        ("1丁目2番地3", "1-2-3"),
        ("1丁目2番地の3", "1-2-3"),
        ("1丁目2-3", "1-2-3"),
        ("123番地", "123"),
        ("123番地の4", "123-4"),
        ("5の2", "5-2"),
        ("1 - 2 - 3", "1-2-3"),
        ("3号館", "3号館"),
        ("1丁目", "1丁目"),
        ("三番町6-3", "三番町6-3"),
    ],
)
def test_canonicalize_block(raw: str, expected: str) -> None:
    assert canonicalize_block(raw) == expected


@pytest.mark.parametrize(
    ("raw", "pref", "city", "town", "block", "building"),
    [
        ("東京都千代田区霞が関三丁目一番一号", "東京都", "千代田区", "霞が関", "3-1-1", None),
        ("東京都千代田区霞が関３－１－１", "東京都", "千代田区", "霞が関", "3-1-1", None),
        (
            "東京都新宿区西新宿二丁目8番1号 都庁第一本庁舎32F",
            "東京都",
            "新宿区",
            "西新宿",
            "2-8-1",
            "都庁第一本庁舎32F",
        ),
        ("大阪府大阪市北区梅田3-1-3", "大阪府", "大阪市北区", "梅田", "3-1-3", None),
        (
            "神奈川県横浜市西区みなとみらい2-2-1-1",
            "神奈川県",
            "横浜市西区",
            "みなとみらい",
            "2-2-1-1",
            None,
        ),
        ("三重県四日市市諏訪町1番5号", "三重県", "四日市市", "諏訪町", "1-5", None),
        ("広島県廿日市市下平良1丁目11番1号", "広島県", "廿日市市", "下平良", "1-11-1", None),
        ("千葉県市川市八幡1-1-1", "千葉県", "市川市", "八幡", "1-1-1", None),
        ("東京都町田市森野2-2-22", "東京都", "町田市", "森野", "2-2-22", None),
        ("東京都東村山市本町1-2-3", "東京都", "東村山市", "本町", "1-2-3", None),
        ("愛知県蒲郡市旭町17-1", "愛知県", "蒲郡市", "旭町", "17-1", None),
        ("奈良県大和郡山市北郡山町248-4", "奈良県", "大和郡山市", "北郡山町", "248-4", None),
        ("群馬県佐波郡玉村町大字下新田201", "群馬県", "佐波郡玉村町", "大字下新田", "201", None),
        ("北海道余市郡余市町朝日町26", "北海道", "余市郡余市町", "朝日町", "26", None),
        ("長野県大町市大町3887", "長野県", "大町市", "大町", "3887", None),
        ("東京都千代田区三番町6-3", "東京都", "千代田区", "三番町", "6-3", None),
        (
            "京都府京都市中京区寺町通御池上る上本能寺前町488",
            "京都府",
            "京都市中京区",
            "寺町通御池上る上本能寺前町",
            "488",
            None,
        ),
        ("東京都港区芝公園4-2-8 3号館", "東京都", "港区", "芝公園", "4-2-8", "3号館"),
    ],
)
def test_structure(
    raw: str, pref: str, city: str, town: str, block: str, building: str | None
) -> None:
    r = normalize_address(raw)
    assert (r.prefecture, r.city, r.town, r.block, r.building) == (
        pref, city, town, block, building,
    )  # fmt: skip


def test_normalized_string_and_code() -> None:
    r = normalize_address("東京都千代田区霞が関三丁目一番一号")
    assert r.normalized == "東京都千代田区霞が関3-1-1"
    assert r.prefecture_code == "13"
    r = normalize_address("東京都新宿区西新宿2-8-1 都庁")
    assert r.normalized == "東京都新宿区西新宿2-8-1 都庁"


@pytest.mark.parametrize(
    ("raw", "postal", "pref"),
    [
        ("千代田区丸の内1-9-1", None, "東京都"),  # 23 区から補完
        ("大阪市北区梅田3-1-3", None, "大阪府"),  # 政令市から補完
        ("京都市中京区寺町通488", None, "京都府"),  # 「京都市」は京都府（京都+市ではない）
        ("横浜市中区港町1-1", None, "神奈川県"),
        ("四日市市諏訪町1-5", None, "三重県"),
        ("神奈川横浜市中区港町1-1", None, "神奈川県"),  # 「県」の欠落
        ("大阪北区梅田1-1", None, "大阪府"),
        ("中央区銀座1-1-1", "104-0061", "東京都"),  # 曖昧な区名は郵便番号から
        ("府中市宮西町2-24", "183-0055", "東京都"),
    ],
)
def test_prefecture_completion(raw: str, postal: str | None, pref: str) -> None:
    r = normalize_address(raw, postal)
    assert r.prefecture == pref
    assert IssueCode.PREFECTURE_COMPLETED in codes(r)


def test_prefecture_missing() -> None:
    r = normalize_address("中央区銀座1-1-1")
    assert r.prefecture is None
    assert IssueCode.PREFECTURE_MISSING in codes(r)


def test_hokkaido_is_not_shortened() -> None:
    r = normalize_address("北海道札幌市中央区北1条西2丁目1")
    assert r.prefecture == "北海道"
    assert r.city == "札幌市中央区"
    assert r.town == "北1条西"
    assert r.block == "2-1"


def test_postal_extracted_from_text() -> None:
    r = normalize_address("〒100-8978 東京都千代田区霞が関3-1-1")
    assert r.postal_code == "100-8978"
    assert r.prefecture == "東京都"
    r = normalize_address("〒１００８９７８　東京都千代田区霞が関3-1-1")
    assert r.postal_code == "100-8978"


@pytest.mark.parametrize("postal", ["100-8978", "1008978", "１００－８９７８", "〒100-8978"])
def test_postal_column_formats(postal: str) -> None:
    r = normalize_address("東京都千代田区霞が関3-1-1", postal)
    assert r.postal_code == "100-8978"
    assert not {IssueCode.POSTAL_CODE_INVALID, IssueCode.POSTAL_PREFECTURE_MISMATCH} & codes(r)


@pytest.mark.parametrize("postal", ["100-897", "12345678", "abc"])
def test_postal_invalid(postal: str) -> None:
    r = normalize_address("東京都千代田区霞が関3-1-1", postal)
    assert IssueCode.POSTAL_CODE_INVALID in codes(r)


def test_postal_prefecture_mismatch() -> None:
    r = normalize_address("東京都港区芝公園4-2-8", "530-0001")
    assert IssueCode.POSTAL_PREFECTURE_MISMATCH in codes(r)


def test_postal_conflict_between_column_and_text() -> None:
    r = normalize_address("〒100-8978 東京都千代田区霞が関3-1-1", "105-0011")
    assert IssueCode.POSTAL_CODE_CONFLICT in codes(r)
    assert r.postal_code == "105-0011"


def test_postal_missing_is_info() -> None:
    r = normalize_address("東京都千代田区霞が関3-1-1")
    assert IssueCode.POSTAL_CODE_MISSING in codes(r)


def test_block_missing() -> None:
    r = normalize_address("東京都千代田区丸の内1丁目")
    assert r.block is None
    assert r.town == "丸の内1丁目"
    assert IssueCode.BLOCK_NUMBER_MISSING in codes(r)


def test_city_not_detected() -> None:
    r = normalize_address("東京都1-1")
    assert IssueCode.CITY_NOT_DETECTED in codes(r)


@pytest.mark.parametrize("raw", ["", None, "   "])
def test_empty(raw: str | None) -> None:
    r = normalize_address(raw)
    assert r.normalized == ""
    assert IssueCode.EMPTY_ADDRESS in codes(r)


def test_postal_only() -> None:
    r = normalize_address("", "100-8978")
    assert r.postal_code == "100-8978"
    assert r.prefecture == "東京都"
    assert IssueCode.EMPTY_ADDRESS in codes(r)


def test_equivalent_addresses_share_match_key() -> None:
    variants = [
        "東京都千代田区霞が関三丁目一番一号",
        "東京都千代田区霞が関3丁目1番1号",
        "東京都千代田区霞が関３－１－１",
        "千代田区霞が関3-1-1",
        "〒100-8978 東京都 千代田区 霞が関 3-1-1",
    ]
    assert len({normalize_address(v).match_key for v in variants}) == 1


def test_to_dict() -> None:
    d = normalize_address("東京都千代田区霞が関3-1-1").to_dict()
    assert d["prefecture_code"] == "13"
    assert isinstance(d["issues"], list)
