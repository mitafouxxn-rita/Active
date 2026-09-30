"""文字列類似度（Levenshtein 距離）。

既定は外部依存なしの純 Python 実装で、``max_distance`` を超えた時点で計算を打ち切る。
``rapidfuzz`` がインストールされていれば（``pip install 'corpnorm[fast]'``）C++ 実装に
自動で切り替わり、重複判定が数十倍高速になる。結果は同一。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any


def _load_rapidfuzz() -> tuple[Any, Any]:
    try:
        from rapidfuzz import process
        from rapidfuzz.distance import Levenshtein
    except ImportError:  # pragma: no cover - rapidfuzz 非導入環境
        return None, None
    return Levenshtein, process


_RF, _rf_process = _load_rapidfuzz()


def levenshtein_py(a: str, b: str, max_distance: int | None = None) -> int:
    """編集距離。``max_distance`` を超える場合は ``max_distance + 1`` を返す。"""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    la, lb = len(a), len(b)
    if max_distance is not None and la - lb > max_distance:
        return max_distance + 1
    if lb == 0:
        return la
    previous = list(range(lb + 1))
    for i in range(1, la + 1):
        current = [i] + [0] * lb
        ca = a[i - 1]
        row_min = i
        for j in range(1, lb + 1):
            cost = 0 if ca == b[j - 1] else 1
            v = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            current[j] = v
            if v < row_min:
                row_min = v
        if max_distance is not None and row_min > max_distance:
            return max_distance + 1
        previous = current
    return previous[lb]


def _levenshtein_rf(a: str, b: str, max_distance: int | None = None) -> int:
    return int(_RF.distance(a, b, score_cutoff=max_distance))


#: 実際に使われる実装（rapidfuzz があればそちら）
levenshtein: Callable[..., int] = _levenshtein_rf if _RF is not None else levenshtein_py
ACCELERATED = _RF is not None


def similarity(a: str, b: str) -> float:
    """0.0〜1.0 の正規化類似度（1 - 距離 / 長い方の長さ）。"""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    longest = max(len(a), len(b))
    return 1.0 - levenshtein(a, b) / longest


def similarity_at_least(a: str, b: str, threshold: float) -> float | None:
    """類似度が ``threshold`` 以上なら値を、未満なら ``None`` を返す（枝刈り付き）。"""
    if not a or not b:
        return None
    la, lb = len(a), len(b)
    longest = la if la > lb else lb
    budget = int((1.0 - threshold) * longest + 1e-9)
    if abs(la - lb) > budget:  # 長さの差だけで閾値を割る組は計算しない
        return None
    d = levenshtein(a, b, max_distance=budget)
    if d > budget:
        return None
    return 1.0 - d / longest


def indices_at_least(
    query: str, choices: Sequence[str], threshold: float, *, accelerated: bool | None = None
) -> list[int]:
    """``choices`` のうち ``query`` との類似度が ``threshold`` 以上の要素の位置。

    rapidfuzz があればループ自体を C++ 側で回す（候補数が多いブロックで効く）。
    """
    use_rf = ACCELERATED if accelerated is None else accelerated and ACCELERATED
    if not query or not choices:
        return []
    if use_rf:  # pragma: no branch
        hits = _rf_process.extract(
            query,
            choices,
            scorer=_RF.normalized_similarity,
            score_cutoff=max(threshold - 1e-9, 0.0),
            limit=None,
        )
        return [idx for _, _, idx in hits]
    return [
        i for i, c in enumerate(choices) if similarity_at_least(query, c, threshold) is not None
    ]
