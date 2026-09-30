from pathlib import Path

import pytest

from corpnorm.address import normalize_address
from corpnorm.data import prefecture_from_postal, short_prefecture
from corpnorm.issues import IssueCode
from corpnorm.postal import (
    PostalDictionary,
    PostalEntry,
    format_postal_code,
    normalize_postal_code,
)

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def pdict() -> PostalDictionary:
    return PostalDictionary.load(FIX / "ken_all_sample.csv")


def codes(r):  # type: ignore[no-untyped-def]
    return {i.code for i in r.issues}


def test_load_utf8_and_sjis() -> None:
    a = PostalDictionary.load(FIX / "ken_all_sample.csv")
    b = PostalDictionary.load(FIX / "ken_all_sample_sjis.csv")
    assert len(a) == len(b) == 10
    assert a.lookup("1000005")[0].town == "丸の内"  # 括弧書きの除去
    assert a.lookup("1000000")[0].town == ""  # 「以下に掲載がない場合」
    assert b.lookup("5300001")[0].city == "大阪市北区"


def test_duplicate_entries_are_ignored() -> None:
    d = PostalDictionary()
    e = PostalEntry("1000005", "東京都", "千代田区", "丸の内")
    d.add(e)
    d.add(e)
    assert len(d) == 1


def test_city_lookup(pdict: PostalDictionary) -> None:
    assert pdict.prefectures_for_city("府中市") == {"東京都", "広島県"}
    assert pdict.match_city("府中市宮西町", "広島県") == "府中市"
    assert pdict.match_city("佐波郡玉村町下新田", None) == "佐波郡玉村町"
    assert pdict.match_city("", None) is None
    assert pdict.match_city("存在しない市", None) is None


def test_dictionary_enables_city_mismatch(pdict: PostalDictionary) -> None:
    ok = normalize_address("東京都千代田区霞が関3-1-1", "100-8978", postal_dict=pdict)
    assert not {IssueCode.POSTAL_CITY_MISMATCH, IssueCode.POSTAL_CODE_UNKNOWN} & codes(ok)
    bad = normalize_address("東京都港区芝公園4-2-8", "100-8978", postal_dict=pdict)
    assert IssueCode.POSTAL_CITY_MISMATCH in codes(bad)


def test_dictionary_pref_mismatch_and_unknown(pdict: PostalDictionary) -> None:
    r = normalize_address("東京都港区芝公園4-2-8", "530-0001", postal_dict=pdict)
    assert IssueCode.POSTAL_PREFECTURE_MISMATCH in codes(r)
    r = normalize_address("東京都港区芝公園4-2-8", "999-9999", postal_dict=pdict)
    assert IssueCode.POSTAL_CODE_UNKNOWN in codes(r)


def test_dictionary_completes_prefecture(pdict: PostalDictionary) -> None:
    # 豊田市は辞書で一意に愛知県
    r = normalize_address("豊田市トヨタ町1", postal_dict=pdict)
    assert r.prefecture == "愛知県"
    # 府中市は曖昧 → 郵便番号から特定
    r = normalize_address("府中市府中町1", "726-0000", postal_dict=pdict)
    assert r.prefecture == "広島県"
    assert r.city == "府中市"


def test_prefix_table() -> None:
    assert prefecture_from_postal("1000005") == "東京都"
    assert prefecture_from_postal("0600001") == "北海道"
    assert prefecture_from_postal("9000001") == "沖縄県"
    assert prefecture_from_postal("9800001") == "宮城県"
    assert prefecture_from_postal("ab") is None
    assert prefecture_from_postal("0000000") is None


def test_helpers() -> None:
    assert normalize_postal_code("〒１００－０００５") == "1000005"
    assert normalize_postal_code(None) is None
    assert normalize_postal_code("-") is None
    assert format_postal_code("1000005") == "100-0005"
    assert short_prefecture("東京都") == "東京"
    assert short_prefecture("北海道") == "北海道"
