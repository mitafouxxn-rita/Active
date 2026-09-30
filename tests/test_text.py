import pytest

from corpnorm.text import fold_small_kana, hiragana_to_katakana, match_key, normalize_width


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ＡＢＣ１２３", "ABC123"),
        ("ｱｲｳｴｵ", "アイウエオ"),
        ("ｶﾞｷﾞｸﾞ", "ガギグ"),
        ("（株）", "(株)"),
        ("㈱", "(株)"),
        ("１－２－３", "1-2-3"),
        ("1‐2―3", "1-2-3"),
        ("1ー2", "1-2"),  # 数字間の長音符はハイフン
        ("ソニー", "ソニー"),  # カタカナの長音は保持
        ("ソニ－", "ソニー"),  # カタカナ直後の全角ハイフンは長音へ
        ("東京　都", "東京都"),  # 日本語間の全角スペースは削除
        ("ABC  Holdings", "ABC Holdings"),  # 英単語間の空白は 1 つに
        ("株式会社 ABC", "株式会社ABC"),
        ("​ABC﻿", "ABC"),  # ゼロ幅文字
        ("A\tB\nC", "A B C"),
        ("10〜20", "10~20"),
        ("", ""),
    ],
)
def test_normalize_width(raw: str, expected: str) -> None:
    assert normalize_width(raw) == expected


def test_keep_cjk_spaces() -> None:
    assert normalize_width("株式会社ABC　東京支店", keep_cjk_spaces=True) == "株式会社ABC 東京支店"


def test_kana_helpers() -> None:
    assert hiragana_to_katakana("やまだ") == "ヤマダ"
    assert fold_small_kana("ショウジ") == "シヨウジ"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("ABC-Holdings", "ａｂｃ　ホールディングス".replace("ホールディングス", "HOLDINGS")),
        ("やまだ", "ヤマダ"),
        ("ｼｮｳｼﾞ", "シヨウジ"),
        ("A&B・C", "ABC"),
    ],
)
def test_match_key_equivalence(a: str, b: str) -> None:
    assert match_key(a) == match_key(b)
