"""郵便番号辞書（日本郵便 KEN_ALL.csv 形式）のローダー。

組み込みの上位 3 桁テーブルは都道府県レベルの整合性チェックしかできないため、
市区町村レベルの照合や都道府県の完全な補完が必要な場合にこの辞書を使う。

KEN_ALL.csv（Shift_JIS）と utf_ken_all.csv（UTF-8）の双方に対応し、
ファイルは 1 行ずつストリームで読み込む。
"""

from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from corpnorm.encoding import open_text

_PAREN_RE = re.compile(r"[（(].*$")
_NO_TOWN = ("以下に掲載がない場合", "の次に番地がくる場合")


@dataclass(frozen=True)
class PostalEntry:
    postal_code: str
    prefecture: str
    city: str
    town: str


def normalize_postal_code(value: str | None) -> str | None:
    """全角・ハイフン・〒を取り除いた数字列（桁数の検証はしない）。"""
    if value is None:
        return None
    from corpnorm.text import normalize_width

    digits = re.sub(r"[^0-9]", "", normalize_width(value))
    return digits or None


def format_postal_code(code7: str) -> str:
    return f"{code7[:3]}-{code7[3:]}"


class PostalDictionary:
    """郵便番号 → (都道府県, 市区町村, 町域) の辞書。"""

    def __init__(self, entries: Iterable[PostalEntry] = ()) -> None:
        self._by_code: dict[str, list[PostalEntry]] = defaultdict(list)
        self._city_prefs: dict[str, set[str]] = defaultdict(set)
        self._cities_by_head: dict[str, list[str]] = defaultdict(list)
        self._size = 0
        for entry in entries:
            self.add(entry)

    def add(self, entry: PostalEntry) -> None:
        pref = sys.intern(entry.prefecture)
        city = sys.intern(entry.city)
        e = PostalEntry(entry.postal_code, pref, city, entry.town)
        bucket = self._by_code[e.postal_code]
        if e not in bucket:
            bucket.append(e)
            self._size += 1
        if city and pref not in self._city_prefs[city]:
            if not self._city_prefs[city]:
                heads = self._cities_by_head[city[0]]
                heads.append(city)
                heads.sort(key=len, reverse=True)
            self._city_prefs[city].add(pref)

    def __len__(self) -> int:
        return self._size

    def lookup(self, code7: str) -> list[PostalEntry]:
        return list(self._by_code.get(code7, ()))

    def prefectures_for_city(self, city: str) -> set[str]:
        return set(self._city_prefs.get(city, ()))

    def match_city(self, text: str, prefecture: str | None = None) -> str | None:
        """``text`` の先頭に一致する最長の市区町村名を返す。"""
        if not text:
            return None
        for city in self._cities_by_head.get(text[0], ()):
            if text.startswith(city) and (
                prefecture is None or prefecture in self._city_prefs[city]
            ):
                return city
        return None

    @classmethod
    def load(cls, path: str | Path, encoding: str = "auto") -> PostalDictionary:
        d = cls()
        for entry in iter_ken_all(path, encoding=encoding):
            d.add(entry)
        return d


def iter_ken_all(path: str | Path, encoding: str = "auto") -> Iterator[PostalEntry]:
    with open_text(path, encoding) as fh:
        for row in csv.reader(fh):
            if len(row) < 9 or not row[2].strip().isdigit():
                continue
            town = row[8].strip()
            if any(t in town for t in _NO_TOWN):
                town = ""
            town = _PAREN_RE.sub("", town)
            yield PostalEntry(row[2].strip(), row[6].strip(), row[7].strip(), town)
