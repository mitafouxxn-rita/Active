"""住所の正規化と郵便番号との整合性チェック。

処理の流れ:

1. 文字幅・ハイフン類の統一、郵便番号（〒123-4567）の抽出
2. 丁目・番地・号の前にある漢数字をアラビア数字へ変換（地名中の漢数字は保持）
3. 「1丁目2番3号」「1丁目2番地の3」→「1-2-3」への統一
4. 都道府県 → 市区町村 → 町域 → 番地 → 建物名 に分解
5. 都道府県の補完（市区町村名・郵便番号から）と郵便番号との整合性チェック
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from corpnorm.data import (
    CITY_TO_PREFECTURE,
    DESIGNATED_CITIES,
    PREFECTURE_CODES,
    PREFECTURES,
    TRICKY_MUNICIPALITIES,
    prefecture_from_postal,
    short_prefecture,
)
from corpnorm.issues import Issue, IssueCode
from corpnorm.postal import PostalDictionary, format_postal_code, normalize_postal_code
from corpnorm.text import match_key, normalize_width

# ---------------------------------------------------------------- 漢数字

_KANJI_DIGIT = {
    "〇": 0, "零": 0, "一": 1, "壱": 1, "二": 2, "弐": 2, "三": 3, "参": 3,
    "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}  # fmt: skip
_KANJI_UNIT = {"十": 10, "拾": 10, "百": 100, "千": 1000}
_KANJI_CHARS = "".join(_KANJI_DIGIT) + "".join(_KANJI_UNIT)
_KNUM = f"[{_KANJI_CHARS}]+"


def kanji_to_int(text: str) -> int:
    """漢数字を整数に変換する。位取り（二十三）と桁並び（二三）の双方に対応。

    >>> kanji_to_int("二十三"), kanji_to_int("百五"), kanji_to_int("一〇五")
    (23, 105, 105)
    """
    if not text or any(c not in _KANJI_DIGIT and c not in _KANJI_UNIT for c in text):
        raise ValueError(f"漢数字ではありません: {text!r}")
    if all(c in _KANJI_DIGIT for c in text):
        return int("".join(str(_KANJI_DIGIT[c]) for c in text))
    total, current = 0, 0
    for c in text:
        if c in _KANJI_DIGIT:
            current = current * 10 + _KANJI_DIGIT[c]
        else:
            total += (current or 1) * _KANJI_UNIT[c]
            current = 0
    return total + current


def _kanji_sub(m: re.Match[str]) -> str:
    return str(kanji_to_int(m.group(1)))


# 丁目・番地・号の直前（「三番町」「一番町」のような町名は除外）
_KANJI_BEFORE_UNIT_RE = re.compile(rf"({_KNUM})(?=丁目|番地|番(?!町)|号|地割)")
# ハイフン区切りの番地（「銀座一-二-三」）
_KANJI_BEFORE_HYPHEN_RE = re.compile(rf"({_KNUM})(?=-[\d{_KANJI_CHARS}])")
_KANJI_AFTER_HYPHEN_RE = re.compile(rf"(?<=\d-)({_KNUM})(?![{_KANJI_CHARS}])")


def convert_kanji_numerals(text: str) -> str:
    s = _KANJI_BEFORE_UNIT_RE.sub(_kanji_sub, text)
    for _ in range(4):  # 「一-二-三」のように連鎖する場合に備えて数回適用
        s2 = _KANJI_AFTER_HYPHEN_RE.sub(_kanji_sub, _KANJI_BEFORE_HYPHEN_RE.sub(_kanji_sub, s))
        if s2 == s:
            break
        s = s2
    return s


# ---------------------------------------------------------------- 番地表記

_BLOCK_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\s*-\s*(?=\d)"), "-"),
    (re.compile(r"(\d+)\s*丁目\s*(?=\d)"), r"\1-"),
    (re.compile(r"(\d+)\s*(?:番地|番(?!町))\s*の?\s*(?=\d)"), r"\1-"),
    (re.compile(r"(?<=\d)\s*の\s*(?=\d)"), "-"),
    (re.compile(r"(\d+)\s*号(?!館|棟|室|線|機|車)"), r"\1"),
    (re.compile(r"(\d+)\s*(?:番地|番(?!町))"), r"\1"),
    (re.compile(r"(\d+)\s*地割\s*(?=\d)"), r"\1-"),
)


def canonicalize_block(text: str) -> str:
    """「1丁目2番3号」「1丁目2番地の3」「1-2-3」を「1-2-3」に統一する。"""
    s = text
    for pattern, repl in _BLOCK_RULES:
        s = pattern.sub(repl, s)
    return s


# 番地の開始位置（「北3条西」のような条丁目の数字や「1丁目」単独は除外）
_BLOCK_RE = re.compile(r"(?<![\d])(\d+(?:-\d+)*)(?![\d条線丁])")
_POSTAL_IN_TEXT_RE = re.compile(r"^\s*〒?\s*(\d{3})\s*-?\s*(\d{4})(?!\d)\s*")

# ---------------------------------------------------------------- 都道府県・市区町村

_PREF_FULL_SORTED = sorted(PREFECTURES, key=len, reverse=True)
_PREF_SHORT = {short_prefecture(p): p for p in PREFECTURES if p != "北海道"}
_TRICKY_SORTED = sorted(TRICKY_MUNICIPALITIES, key=len, reverse=True)
_DESIGNATED_RE = re.compile(
    "^(" + "|".join(sorted(DESIGNATED_CITIES, key=len, reverse=True)) + r")(.+?区)?"
)
_COUNTY_RE = re.compile(r"^(.+?郡)(.+?[町村])")
_GENERIC_CITY_RE = re.compile(r"^(.+?[市区町村])")
_CITY_SUFFIXES = "市区町村郡"


def _split_prefecture(s: str) -> tuple[str | None, str, bool]:
    """(都道府県, 残り, 「都/府/県」を補ったか) を返す。"""
    for pref in _PREF_FULL_SORTED:
        if s.startswith(pref):
            return pref, s[len(pref) :], False
    # 「京都市」は京都府の市。都道府県の省略ではない
    for short, pref in _PREF_SHORT.items():
        if s.startswith(short):
            rest = s[len(short) :]
            if rest and rest[0] not in _CITY_SUFFIXES and not s.startswith(short + "市"):
                return pref, rest, True
    return None, s, False


def _split_city(
    s: str, prefecture: str | None, postal_dict: PostalDictionary | None
) -> tuple[str | None, str]:
    if postal_dict is not None:
        city = postal_dict.match_city(s, prefecture)
        if city:
            return city, s[len(city) :]
    for name in _TRICKY_SORTED:
        if s.startswith(name) and not name.endswith(("町", "村")):
            return name, s[len(name) :]
    m = _DESIGNATED_RE.match(s)
    if m:
        city = m.group(1) + (m.group(2) or "")
        return city, s[len(city) :]
    m = _COUNTY_RE.match(s)
    if m:
        county, rest = m.group(1), s[len(m.group(1)) :]
        for name in _TRICKY_SORTED:
            if rest.startswith(name):
                return county + name, rest[len(name) :]
        city = county + m.group(2)
        return city, s[len(city) :]
    m = _GENERIC_CITY_RE.match(s)
    if m and not re.search(r"\d", m.group(1)):
        return m.group(1), s[len(m.group(1)) :]
    return None, s


def _infer_prefecture_from_city(city: str, postal_dict: PostalDictionary | None) -> str | None:
    if postal_dict is not None:
        prefs = postal_dict.prefectures_for_city(city)
        if len(prefs) == 1:
            return next(iter(prefs))
    base = _DESIGNATED_RE.match(city)
    if base:
        return DESIGNATED_CITIES[base.group(1)]
    return CITY_TO_PREFECTURE.get(city)


# ---------------------------------------------------------------- 本体


@dataclass(frozen=True)
class NormalizedAddress:
    original: str
    normalized: str
    postal_code: str | None
    prefecture: str | None
    prefecture_code: str | None
    city: str | None
    town: str | None
    block: str | None
    building: str | None
    match_key: str
    issues: tuple[Issue, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "original": self.original,
            "normalized": self.normalized,
            "postal_code": self.postal_code,
            "prefecture": self.prefecture,
            "prefecture_code": self.prefecture_code,
            "city": self.city,
            "town": self.town,
            "block": self.block,
            "building": self.building,
            "match_key": self.match_key,
            "issues": [i.to_dict() for i in self.issues],
        }


def _clean(value: str) -> str | None:
    v = normalize_width(value).strip(" -,、")
    return v or None


def normalize_address(
    raw: str | None,
    postal_code: str | None = None,
    *,
    postal_dict: PostalDictionary | None = None,
) -> NormalizedAddress:
    """住所を正規化し、郵便番号との整合性を検証する。

    >>> normalize_address("東京都千代田区霞が関三丁目一番一号").normalized
    '東京都千代田区霞が関3-1-1'
    """
    original = raw or ""
    issues: list[Issue] = []
    s = normalize_width(original, keep_cjk_spaces=True)

    # --- 郵便番号
    embedded: str | None = None
    m_postal = _POSTAL_IN_TEXT_RE.match(s)
    if m_postal:
        embedded = m_postal.group(1) + m_postal.group(2)
        s = s[m_postal.end() :]
    s = s.lstrip("〒 ")

    explicit = normalize_postal_code(postal_code)
    code7: str | None = None
    if explicit is None and postal_code is not None and normalize_width(postal_code).strip("〒 -"):
        explicit = ""  # 数字を含まない値（"abc" 等）は不正として扱う
    if explicit is not None:
        if len(explicit) != 7:
            issues.append(
                Issue.of(
                    IssueCode.POSTAL_CODE_INVALID,
                    f"郵便番号は 7 桁である必要があります: {postal_code!r}",
                    "postal_code",
                )
            )
        else:
            code7 = explicit
        if embedded and code7 and embedded != code7:
            issues.append(
                Issue.of(
                    IssueCode.POSTAL_CODE_CONFLICT,
                    f"郵便番号欄（{format_postal_code(code7)}）と住所中の郵便番号"
                    f"（{format_postal_code(embedded)}）が一致しません",
                    "postal_code",
                )
            )
    code7 = code7 or embedded

    if not s.strip():
        if code7 is None:
            issues.append(Issue.of(IssueCode.EMPTY_ADDRESS, "住所が空です", "address"))
        else:
            issues.append(
                Issue.of(IssueCode.EMPTY_ADDRESS, "住所が空です（郵便番号のみ）", "address")
            )
        return NormalizedAddress(
            original=original,
            normalized="",
            postal_code=format_postal_code(code7) if code7 else None,
            prefecture=prefecture_from_postal(code7) if code7 else None,
            prefecture_code=None,
            city=None,
            town=None,
            block=None,
            building=None,
            match_key="",
            issues=tuple(issues),
        )

    # --- 数字表記の統一
    s = canonicalize_block(convert_kanji_numerals(s))

    # --- 都道府県・市区町村
    compact = re.sub(r"(?<=[^\x00-\x7f]) (?=[^\x00-\x7f])", "", s.strip())
    prefecture, rest, suffix_added = _split_prefecture(compact)
    city, rest = _split_city(rest, prefecture, postal_dict)
    if suffix_added and prefecture:
        issues.append(
            Issue.of(
                IssueCode.PREFECTURE_COMPLETED,
                f"都道府県名を「{prefecture}」に補完しました",
                "address",
            )
        )

    if prefecture is None:
        inferred = _infer_prefecture_from_city(city, postal_dict) if city else None
        source = "市区町村名"
        if inferred is None and code7 is not None:
            if postal_dict is not None:
                prefs = {e.prefecture for e in postal_dict.lookup(code7)}
                inferred = next(iter(prefs)) if len(prefs) == 1 else None
            if inferred is None:
                inferred = prefecture_from_postal(code7)
            source = "郵便番号"
        if inferred:
            prefecture = inferred
            issues.append(
                Issue.of(
                    IssueCode.PREFECTURE_COMPLETED,
                    f"都道府県「{inferred}」を{source}から補完しました",
                    "address",
                )
            )
            if city is None:
                city, rest = _split_city(rest, prefecture, postal_dict)
        else:
            issues.append(
                Issue.of(IssueCode.PREFECTURE_MISSING, "都道府県を特定できません", "address")
            )

    if city is None:
        issues.append(Issue.of(IssueCode.CITY_NOT_DETECTED, "市区町村を特定できません", "address"))

    # --- 町域・番地・建物
    m_block = _BLOCK_RE.search(rest)
    if m_block:
        town = _clean(rest[: m_block.start()])
        block: str | None = m_block.group(1)
        building = _clean(rest[m_block.end() :])
    else:
        town, block, building = _clean(rest), None, None
        issues.append(Issue.of(IssueCode.BLOCK_NUMBER_MISSING, "番地が見つかりません", "address"))

    # --- 郵便番号の整合性
    if code7 is None:
        if explicit is None:
            issues.append(
                Issue.of(IssueCode.POSTAL_CODE_MISSING, "郵便番号がありません", "postal_code")
            )
    elif postal_dict is not None:
        entries = postal_dict.lookup(code7)
        if not entries:
            issues.append(
                Issue.of(
                    IssueCode.POSTAL_CODE_UNKNOWN,
                    f"郵便番号 {format_postal_code(code7)} は辞書に存在しません",
                    "postal_code",
                )
            )
        elif prefecture and prefecture not in {e.prefecture for e in entries}:
            issues.append(_pref_mismatch(code7, prefecture, entries[0].prefecture))
        elif city and city not in {e.city for e in entries}:
            issues.append(
                Issue.of(
                    IssueCode.POSTAL_CITY_MISMATCH,
                    f"郵便番号 {format_postal_code(code7)} は「{entries[0].city}」ですが、"
                    f"住所は「{city}」です",
                    "postal_code",
                )
            )
    else:
        expected = prefecture_from_postal(code7)
        if prefecture and expected and expected != prefecture:
            issues.append(_pref_mismatch(code7, prefecture, expected))

    parts = [prefecture or "", city or "", town or "", block or ""]
    normalized = "".join(parts)
    if building:
        normalized = f"{normalized} {building}" if normalized else building

    return NormalizedAddress(
        original=original,
        normalized=normalized,
        postal_code=format_postal_code(code7) if code7 else None,
        prefecture=prefecture,
        prefecture_code=PREFECTURE_CODES.get(prefecture) if prefecture else None,
        city=city,
        town=town,
        block=block,
        building=building,
        match_key=match_key("".join(parts)),
        issues=tuple(issues),
    )


def _pref_mismatch(code7: str, actual: str, expected: str) -> Issue:
    return Issue.of(
        IssueCode.POSTAL_PREFECTURE_MISMATCH,
        f"郵便番号 {format_postal_code(code7)} は「{expected}」の番号ですが、"
        f"住所は「{actual}」です",
        "postal_code",
    )
