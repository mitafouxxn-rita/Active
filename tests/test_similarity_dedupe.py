import pytest

from corpnorm.dedupe import DuplicateDetector
from corpnorm.similarity import levenshtein, levenshtein_py, similarity, similarity_at_least

IMPLS = [levenshtein_py, levenshtein]  # 純 Python 実装と実行時の実装（rapidfuzz）の双方


@pytest.mark.parametrize(
    ("a", "b", "d"),
    [
        ("", "", 0), ("abc", "", 3), ("", "abc", 3), ("kitten", "sitting", 3),
        ("flaw", "lawn", 2), ("山田商事", "山田商亊", 1), ("ABC", "ABC", 0), ("ab", "ba", 2),
    ],
)  # fmt: skip
@pytest.mark.parametrize("lev", IMPLS)
def test_levenshtein(lev, a: str, b: str, d: int) -> None:  # type: ignore[no-untyped-def]
    assert lev(a, b) == d
    assert lev(b, a) == d


@pytest.mark.parametrize("lev", IMPLS)
def test_levenshtein_cutoff(lev) -> None:  # type: ignore[no-untyped-def]
    assert lev("kitten", "sitting", max_distance=1) == 2
    assert lev("a", "abcdef", max_distance=2) == 3
    assert lev("kitten", "sitting", max_distance=3) == 3
    assert lev("abcdef", "badcfe", max_distance=1) == 2


def test_similarity() -> None:
    assert similarity("", "") == 1.0
    assert similarity("a", "") == 0.0
    assert similarity("山田商事", "山田商亊") == pytest.approx(0.75)
    assert similarity_at_least("山田商事", "山田商亊", 0.7) == pytest.approx(0.75)
    assert similarity_at_least("山田商事", "田中工務店", 0.7) is None
    assert similarity_at_least("", "a", 0.5) is None


def test_exact_duplicate() -> None:
    d = DuplicateDetector()
    assert d.check_and_add(1, "山田商事", "大阪府大阪市北区梅田3-1-3", region="大阪府") is None
    m = d.check_and_add(2, "山田商事", "大阪府大阪市北区梅田3-1-3", region="大阪府")
    assert m is not None and m.kind == "exact" and m.of_row == 1 and m.score == 1.0
    assert len(d) == 2


def test_fuzzy_duplicate_with_typo() -> None:
    d = DuplicateDetector(threshold=0.85)
    d.check_and_add(1, "ヤマダショウジトウキョウ", "東京都千代田区丸の内1-1-1", region="東京都")
    m = d.check_and_add(2, "ヤマダショウジトウキヨウ", "東京都千代田区丸の内1-1-1", region="東京都")
    assert m is not None and m.kind == "fuzzy" and 0.85 <= m.score < 1.0


def test_typo_in_first_chars_found_via_second_block() -> None:
    d = DuplicateDetector(threshold=0.8)
    d.check_and_add(1, "サンプルホールディングス")
    m = d.check_and_add(2, "ザンプルホールディングス")
    assert m is not None and m.of_row == 1


def test_different_address_lowers_score() -> None:
    d = DuplicateDetector(threshold=0.9)
    d.check_and_add(1, "山田商事", "大阪府大阪市北区梅田3-1-3", region="大阪府")
    assert d.check_and_add(2, "山田商事", "大阪府堺市堺区南瓦町3-1", region="大阪府") is None


def test_common_suffix_does_not_create_candidates() -> None:
    d = DuplicateDetector(threshold=0.8)
    d.check_and_add(1, "アオバ商事")
    # 末尾（商事）はブロックキーに使わないため、比較対象にすら入らない
    assert d.check_and_add(2, "ミドリ商事") is None
    assert all("商事" not in k for k in d._blocks)


def test_different_region_not_compared() -> None:
    d = DuplicateDetector(threshold=0.8)
    d.check_and_add(1, "ヤマダショウジ", region="東京都")
    assert d.check_and_add(2, "ヤマダショウジー", region="大阪府") is None


def test_corporate_number_duplicate() -> None:
    d = DuplicateDetector()
    d.check_and_add(1, "A社", corporate_number="1180301018771", record_id="X1")
    m = d.check_and_add(2, "全然違う名前", corporate_number="1180301018771")
    assert m is not None and m.kind == "corporate_number" and m.of_id == "X1"


def test_variant_penalty_for_prefix_vs_suffix() -> None:
    d = DuplicateDetector(threshold=0.9)
    d.check_and_add(1, "山田商事", variant="株式会社:prefix")
    m = d.check_and_add(2, "山田商事", variant="株式会社:suffix")
    assert m is not None and m.kind == "fuzzy" and m.score == pytest.approx(0.95)
    d2 = DuplicateDetector(threshold=0.96)
    d2.check_and_add(1, "山田商事", variant="株式会社:prefix")
    assert d2.check_and_add(2, "山田商事", variant="株式会社:suffix") is None


def test_empty_and_short_keys() -> None:
    d = DuplicateDetector()
    assert d.check_and_add(1, "") is None
    assert d.check_and_add(2, "") is None
    assert d.check_and_add(3, "A") is None
    assert d.check_and_add(4, "A") is not None


def test_block_size_is_bounded() -> None:
    d = DuplicateDetector(max_block_size=3)
    for i in range(10):
        d.check_and_add(i, f"AA{i:03d}XYZ")
    assert all(len(b) <= 3 for b in d._blocks.values())
    assert d.check_and_add(99, "AA009XYZ") is not None  # 直近の行は保持されている


@pytest.mark.parametrize("kw", [{"threshold": 0}, {"threshold": 1.1}, {"name_weight": 2}])
def test_invalid_params(kw: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        DuplicateDetector(**kw)  # type: ignore[arg-type]


def test_indices_at_least_matches_python_fallback() -> None:
    from corpnorm.similarity import indices_at_least

    choices = ["ヤマダシヨウジ", "ヤマダシヨウシ", "タナカコウムテン", "", "ヤマダ"]
    for t in (0.5, 0.8, 0.9, 1.0):
        fast = indices_at_least("ヤマダシヨウジ", choices, t)
        slow = indices_at_least("ヤマダシヨウジ", choices, t, accelerated=False)
        assert sorted(fast) == sorted(slow)
    assert indices_at_least("", choices, 0.5) == []
