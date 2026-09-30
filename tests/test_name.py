import pytest

from corpnorm.issues import IssueCode
from corpnorm.name import legal_form_names, normalize_name


def codes(r):  # type: ignore[no-untyped-def]
    return {i.code for i in r.issues}


@pytest.mark.parametrize(
    ("raw", "normalized", "core", "legal", "pos"),
    [
        # 前株 / 後株（正式表記）
        ("株式会社山田商事", "株式会社山田商事", "山田商事", "株式会社", "prefix"),
        ("山田商事株式会社", "山田商事株式会社", "山田商事", "株式会社", "suffix"),
        # 括弧略称（全角・半角・丸囲み・閉じ括弧なし）
        ("（株）山田商事", "株式会社山田商事", "山田商事", "株式会社", "prefix"),
        ("(株)山田商事", "株式会社山田商事", "山田商事", "株式会社", "prefix"),
        ("㈱山田商事", "株式会社山田商事", "山田商事", "株式会社", "prefix"),
        ("山田商事㈱", "山田商事株式会社", "山田商事", "株式会社", "suffix"),
        ("山田商事(株", "山田商事株式会社", "山田商事", "株式会社", "suffix"),
        ("株)山田商事", "株式会社山田商事", "山田商事", "株式会社", "prefix"),
        ("㍿山田商事", "株式会社山田商事", "山田商事", "株式会社", "prefix"),
        ("(有)田中工務店", "有限会社田中工務店", "田中工務店", "有限会社", "prefix"),
        ("㈲田中工務店", "有限会社田中工務店", "田中工務店", "有限会社", "prefix"),
        ("田中工務店（有）", "田中工務店有限会社", "田中工務店", "有限会社", "suffix"),
        ("(同)ABC", "合同会社ABC", "ABC", "合同会社", "prefix"),
        ("(資)丸山酒造", "合資会社丸山酒造", "丸山酒造", "合資会社", "prefix"),
        ("(名)丸山", "合名会社丸山", "丸山", "合名会社", "prefix"),
        ("(一社)日本協会", "一般社団法人日本協会", "日本協会", "一般社団法人", "prefix"),
        ("(公財)日本財団", "公益財団法人日本財団", "日本財団", "公益財団法人", "prefix"),
        (
            "(特非)かけはし",
            "特定非営利活動法人かけはし",
            "かけはし",
            "特定非営利活動法人",
            "prefix",
        ),
        (
            "NPO法人かけはし",
            "特定非営利活動法人かけはし",
            "かけはし",
            "特定非営利活動法人",
            "prefix",
        ),
        ("(医)健康会", "医療法人健康会", "健康会", "医療法人", "prefix"),
        ("(福)ひまわり会", "社会福祉法人ひまわり会", "ひまわり会", "社会福祉法人", "prefix"),
        ("(学)青葉学園", "学校法人青葉学園", "青葉学園", "学校法人", "prefix"),
        # 長い法人格を優先（医療法人社団 > 医療法人）
        ("医療法人社団 健康会", "医療法人社団健康会", "健康会", "医療法人社団", "prefix"),
        (
            "地方独立行政法人東京都立病院機構",
            "地方独立行政法人東京都立病院機構",
            "東京都立病院機構",
            "地方独立行政法人",
            "prefix",
        ),
        (
            "独立行政法人国際協力機構",
            "独立行政法人国際協力機構",
            "国際協力機構",
            "独立行政法人",
            "prefix",
        ),
        # 全銀協カナ略語（半角カナ）
        ("ｶ)ﾔﾏﾀﾞｼｮｳｼﾞ", "株式会社ヤマダショウジ", "ヤマダショウジ", "株式会社", "prefix"),
        ("ﾔﾏﾀﾞｼｮｳｼﾞ(ｶ", "ヤマダショウジ株式会社", "ヤマダショウジ", "株式会社", "suffix"),
        ("ﾔﾏﾀﾞｼｮｳｼﾞ(ｶ)", "ヤマダショウジ株式会社", "ヤマダショウジ", "株式会社", "suffix"),
        ("(ｶ)ﾔﾏﾀﾞ", "株式会社ヤマダ", "ヤマダ", "株式会社", "prefix"),
        ("ﾕ)ﾀﾅｶｺｳﾑﾃﾝ", "有限会社タナカコウムテン", "タナカコウムテン", "有限会社", "prefix"),
        ("ﾄﾞ)ｴｰﾋﾞｰｼｰ", "合同会社エービーシー", "エービーシー", "合同会社", "prefix"),
        ("ｲ)ｹﾝｺｳｶｲ", "医療法人ケンコウカイ", "ケンコウカイ", "医療法人", "prefix"),
        ("ﾄｸﾋ)ｶｹﾊｼ", "特定非営利活動法人カケハシ", "カケハシ", "特定非営利活動法人", "prefix"),
        # 空白混入
        ("株式 会社 山田商事", "株式会社山田商事", "山田商事", "株式会社", "prefix"),
        ("山田商事 株式会社", "山田商事株式会社", "山田商事", "株式会社", "suffix"),
        ("　（株）　山田商事　", "株式会社山田商事", "山田商事", "株式会社", "prefix"),
        # 全角英数字
        ("株式会社ＮＴＴドコモ", "株式会社NTTドコモ", "NTTドコモ", "株式会社", "prefix"),
        ("ＡＢＣ（株）", "ABC株式会社", "ABC", "株式会社", "suffix"),
    ],
)
def test_legal_form_variants(raw: str, normalized: str, core: str, legal: str, pos: str) -> None:
    r = normalize_name(raw)
    assert r.normalized == normalized
    assert r.core == core
    assert r.legal_form == legal
    assert r.legal_form_position == pos


@pytest.mark.parametrize(
    ("raw", "normalized", "branch"),
    [
        ("ABC株式会社 東京支店", "ABC株式会社", "東京支店"),
        ("ABC株式会社東京支店", "ABC株式会社", "東京支店"),
        ("株式会社ABC　大阪営業所", "株式会社ABC", "大阪営業所"),
        ("(株)ABC 名古屋工場", "株式会社ABC", "名古屋工場"),
    ],
)
def test_branch_split(raw: str, normalized: str, branch: str) -> None:
    r = normalize_name(raw)
    assert r.normalized == normalized
    assert r.branch == branch


def test_same_match_key_across_variants() -> None:
    variants = ["株式会社山田商事", "(株)山田商事", "山田商事㈱", "山田商事 株式会社", "ヤマダ"]
    keys = {normalize_name(v).match_key for v in variants[:4]}
    assert keys == {"山田商事"}


def test_empty_name() -> None:
    for raw in ("", None, "　 "):
        r = normalize_name(raw)
        assert r.normalized == ""
        assert IssueCode.EMPTY_NAME in codes(r)


def test_no_legal_form_is_info() -> None:
    r = normalize_name("山田屋")
    assert r.legal_form is None
    assert r.normalized == "山田屋"
    assert IssueCode.NO_LEGAL_FORM in codes(r)


def test_legal_form_only_is_not_split() -> None:
    r = normalize_name("(有)")
    assert r.legal_form is None
    assert IssueCode.NO_LEGAL_FORM in codes(r)


def test_multiple_legal_forms() -> None:
    r = normalize_name("株式会社ABC株式会社")
    assert r.normalized == "株式会社ABC"
    assert IssueCode.MULTIPLE_LEGAL_FORMS in codes(r)


def test_issue_codes_for_expansion() -> None:
    assert IssueCode.ABBREVIATED_LEGAL_FORM in codes(normalize_name("(株)ABC"))
    assert IssueCode.BANK_KANA_FORMAT in codes(normalize_name("ｶ)ABC"))
    assert codes(normalize_name("株式会社ABC")) == set()


@pytest.mark.parametrize(
    ("raw", "core", "legal", "suffix"),
    [
        ("Sony Group Corporation", "Sony Group", None, "Corporation"),
        ("ABC Co., Ltd.", "ABC", None, "Co., Ltd."),
        ("ABC Co.,Ltd.", "ABC", None, "Co.,Ltd."),
        ("Example Inc.", "Example", None, "Inc."),
        ("Toyota K.K.", "Toyota", "株式会社", "K.K."),
        ("Acme Kabushiki Kaisha", "Acme", "株式会社", "Kabushiki Kaisha"),
        ("Acme LLC", "Acme", "合同会社", "LLC"),
    ],
)
def test_english_suffix(raw: str, core: str, legal: str | None, suffix: str) -> None:
    r = normalize_name(raw)
    assert r.core == core
    assert r.legal_form == legal
    assert r.english_suffix == suffix
    assert r.normalized == f"{core} {suffix}"
    assert IssueCode.ENGLISH_LEGAL_SUFFIX in codes(r)


def test_words_resembling_legal_forms_are_kept() -> None:
    # 「株式会社」を含まない一般名詞は分割しない
    assert normalize_name("株主総会研究会").legal_form is None
    assert normalize_name("名古屋商店").legal_form is None
    assert normalize_name("カメラのキタムラ").legal_form is None


def test_to_dict_is_serializable() -> None:
    import json

    d = normalize_name("(株)ABC").to_dict()
    assert json.loads(json.dumps(d, ensure_ascii=False))["legal_form"] == "株式会社"


def test_legal_form_names() -> None:
    names = legal_form_names()
    assert "株式会社" in names and "特定非営利活動法人" in names
