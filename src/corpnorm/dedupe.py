"""重複候補の検出（ストリーム処理向け）。

全件の総当たり比較（O(n²)）は数万行で破綻するため、ブロッキングで比較対象を絞る:

* 完全一致: (法人番号) / (名称キー + 都道府県 + 市区町村) のハッシュ索引
* あいまい一致: 都道府県 × 名称キーの「1〜2 文字目」/「3〜4 文字目」のブロック内だけを
  Levenshtein（枝刈り付き）で比較する。どちらか一方のブロックが一致すれば比較対象に
  入るため、先頭付近の 1 文字の誤字でも取りこぼさない。末尾（「商事」「工業」等）は
  値の偏りが大きくブロックが肥大化するため使わない

索引には行番号と短い比較キーのみを保持し、元の行データは保持しない。
ブロックあたりの保持件数には上限を設け、極端に偏ったデータでも計算量を抑える。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from corpnorm.similarity import indices_at_least, similarity, similarity_at_least


@dataclass(frozen=True)
class DuplicateMatch:
    of_row: int
    score: float
    kind: str  # "corporate_number" | "exact" | "fuzzy"
    of_id: str | None = None


@dataclass(frozen=True, slots=True)
class _Entry:
    row: int
    name_key: str
    address_key: str
    record_id: str | None
    variant: str


class _Block:
    """比較キーと行情報を並列に保持するリングバッファ（古いものから押し出す）。"""

    __slots__ = ("entries", "keys")

    def __init__(self, maxlen: int) -> None:
        self.entries: deque[_Entry] = deque(maxlen=maxlen)
        self.keys: deque[str] = deque(maxlen=maxlen)

    def append(self, entry: _Entry) -> None:
        self.entries.append(entry)
        self.keys.append(entry.name_key)

    def __len__(self) -> int:
        return len(self.entries)


class DuplicateDetector:
    def __init__(
        self,
        threshold: float = 0.88,
        *,
        name_weight: float = 0.7,
        max_block_size: int = 1000,
        variant_penalty: float = 0.95,
    ) -> None:
        if not 0.0 < threshold <= 1.0:
            raise ValueError("threshold は 0 より大きく 1 以下で指定してください")
        if not 0.0 <= name_weight <= 1.0:
            raise ValueError("name_weight は 0〜1 で指定してください")
        self.threshold = threshold
        self.name_weight = name_weight
        self.max_block_size = max_block_size
        self.variant_penalty = variant_penalty
        # 住所が完全一致しても閾値に届かない名称類似度の下限
        w = name_weight
        self._name_floor = max(0.0, (threshold - (1.0 - w)) / w) if w else 0.0
        self._by_number: dict[str, _Entry] = {}
        self._exact: dict[tuple[str, str, str], _Entry] = {}
        self._blocks: dict[str, _Block] = {}
        self._rows = 0

    def __len__(self) -> int:
        return self._rows

    @staticmethod
    def _block_keys(name_key: str, region: str) -> tuple[str, ...]:
        if len(name_key) < 4:
            return (f"{region}|={name_key[:2]}",)
        return (f"{region}|^{name_key[:2]}", f"{region}|+{name_key[2:4]}")

    def check_and_add(
        self,
        row: int,
        name_key: str,
        address_key: str = "",
        *,
        region: str = "",
        corporate_number: str | None = None,
        record_id: str | None = None,
        variant: str = "",
    ) -> DuplicateMatch | None:
        """既存の行と重複していればその情報を返し、索引に追加する。

        ``variant`` は法人格と前株／後株の組（例: ``株式会社:prefix``）。
        「株式会社山田商事」と「山田商事株式会社」は別法人でありうるため、
        variant が異なる組は完全一致とせず、類似度に ``variant_penalty`` を掛ける。
        """
        entry = _Entry(row, name_key, address_key, record_id, variant)
        self._rows += 1
        match = self._find(entry, region, corporate_number)
        self._add(entry, region, corporate_number)
        return match

    def _find(
        self, entry: _Entry, region: str, corporate_number: str | None
    ) -> DuplicateMatch | None:
        if corporate_number and corporate_number in self._by_number:
            by_num = self._by_number[corporate_number]
            return DuplicateMatch(by_num.row, 1.0, "corporate_number", by_num.record_id)
        if not entry.name_key:
            return None
        hit = self._exact.get((entry.name_key, entry.address_key, entry.variant))
        if hit is not None:
            return DuplicateMatch(hit.row, 1.0, "exact", hit.record_id)

        best: tuple[float, _Entry] | None = None
        seen: set[int] = set()
        floor = self._name_floor if entry.address_key else self.threshold
        for bk in self._block_keys(entry.name_key, region):
            block = self._blocks.get(bk)
            if block is None:
                continue
            # 名称だけで足切りし（C++ 側で一括計算）、残った少数の候補だけ住所込みで採点する
            for idx in indices_at_least(entry.name_key, block.keys, floor):
                cand = block.entries[idx]
                if cand.row in seen:
                    continue
                seen.add(cand.row)
                score = self._score(entry, cand)
                if score is not None and (best is None or score > best[0]):
                    best = (score, cand)
        if best is None:
            return None
        return DuplicateMatch(best[1].row, round(best[0], 4), "fuzzy", best[1].record_id)

    def _score(self, a: _Entry, b: _Entry) -> float | None:
        if a.address_key and b.address_key:
            # 名称の類似度だけで閾値に届かない候補は住所を見るまでもなく除外する
            w = self.name_weight
            name_sim = similarity_at_least(a.name_key, b.name_key, self._name_floor)
            if name_sim is None:
                return None
            score = w * name_sim + (1.0 - w) * similarity(a.address_key, b.address_key)
        else:
            s = similarity_at_least(a.name_key, b.name_key, self.threshold)
            if s is None:
                return None
            score = s
        if a.variant and b.variant and a.variant != b.variant:
            score *= self.variant_penalty
        return score if score >= self.threshold else None

    def _add(self, entry: _Entry, region: str, corporate_number: str | None) -> None:
        if corporate_number:
            self._by_number.setdefault(corporate_number, entry)
        if not entry.name_key:
            return
        self._exact.setdefault((entry.name_key, entry.address_key, entry.variant), entry)
        for bk in self._block_keys(entry.name_key, region):
            blk = self._blocks.get(bk)
            if blk is None:
                blk = self._blocks[bk] = _Block(self.max_block_size)
            blk.append(entry)


__all__ = ["DuplicateDetector", "DuplicateMatch"]
