"""文字種の正規化ユーティリティ（全角・半角、ハイフン類、空白、かな）。"""

from __future__ import annotations

import re
import unicodedata

# 長音符（ー）は含めない。カタカナ語の長音を壊さないため数字間でのみ別途変換する。
_HYPHEN_LIKE = "‐‑‒–—―−⁃˗﹘﹣－─━ーｰ"
_HYPHEN_STRICT = _HYPHEN_LIKE.replace("ー", "").replace("ｰ", "")
_HYPHEN_STRICT_RE = re.compile(f"[{_HYPHEN_STRICT}]")
_CHOON_BETWEEN_DIGITS_RE = re.compile(r"(?<=\d)[ー\-]+(?=\d)")
_HYPHEN_AFTER_KATAKANA_RE = re.compile(r"(?<=[ァ-ヴ])-(?![\d])")
_INVISIBLE_RE = re.compile("[​‌‍⁠﻿­]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACES_RE = re.compile(r"\s+")
# 非 ASCII（日本語）文字に隣接する空白は意味を持たないため削除する
_SPACE_AROUND_CJK_RE = re.compile(
    r"(?<=[^\x00-\x7f]) (?=[^\x00-\x7f])|(?<=[^\x00-\x7f]) (?=[\x00-\x7f])"
    r"|(?<=[\x00-\x7f]) (?=[^\x00-\x7f])"
)
_WAVE_RE = re.compile("[〜～∼∾]")

_SMALL_KANA = str.maketrans("ァィゥェォッャュョヮヵヶ", "アイウエオツヤユヨワカケ")


def normalize_width(text: str, *, keep_cjk_spaces: bool = False) -> str:
    """NFKC を基本に、ハイフン・空白・不可視文字の揺れを統一する。

    - 全角英数字・記号 → 半角、半角カナ → 全角カナ
    - 各種ハイフン・ダッシュ → ``-``（カタカナ直後はそのまま長音 ``ー``）
    - 波ダッシュ類 → ``~``
    - 連続空白の圧縮、日本語に隣接する空白の除去
    """
    if not text:
        return ""
    s = _INVISIBLE_RE.sub("", text)
    s = _CONTROL_RE.sub("", s)
    s = unicodedata.normalize("NFKC", s)
    s = _HYPHEN_STRICT_RE.sub("-", s)
    s = _WAVE_RE.sub("~", s)
    s = _CHOON_BETWEEN_DIGITS_RE.sub("-", s)
    s = _HYPHEN_AFTER_KATAKANA_RE.sub("ー", s)
    s = _SPACES_RE.sub(" ", s).strip()
    if not keep_cjk_spaces:
        s = _SPACE_AROUND_CJK_RE.sub("", s)
    return s


def hiragana_to_katakana(text: str) -> str:
    return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in text)


def fold_small_kana(text: str) -> str:
    """小書きかなを大書きに寄せる（全銀フォーマットのカナは小書きを持たないため）。"""
    return text.translate(_SMALL_KANA)


_KEY_STRIP_RE = re.compile(r"[\s\-_.,，、。・･'\"`’”“()\[\]{}<>「」『』【】〔〕/\\|!?:;&＆+*#@=~]")


def match_key(text: str) -> str:
    """名寄せ用の比較キー。表記揺れの影響を受ける記号・空白・大小文字を落とす。"""
    s = normalize_width(text)
    s = hiragana_to_katakana(s)
    s = fold_small_kana(s)
    s = s.upper()
    return _KEY_STRIP_RE.sub("", s)


def is_katakana_only(text: str) -> bool:
    stripped = re.sub(r"[\s\-ー・.()]", "", text)
    return bool(stripped) and all("ァ" <= c <= "ヺ" for c in stripped)
