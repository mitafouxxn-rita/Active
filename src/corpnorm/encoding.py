"""入出力の文字コード判定。

業務 CSV は UTF-8（BOM 付き含む）と Shift_JIS（cp932）が混在するため、
先頭のサンプルだけを読んで判定し、ファイル全体をメモリに載せない。
"""

from __future__ import annotations

import codecs
import io
import sys
from pathlib import Path
from typing import IO

SAMPLE_BYTES = 256 * 1024
_ALIASES = {
    "sjis": "cp932",
    "shift_jis": "cp932",
    "shift-jis": "cp932",
    "ms932": "cp932",
    "windows-31j": "cp932",
    "utf8": "utf-8",
}


def canonical_encoding(name: str) -> str:
    """エンコーディング名を検証し正規化する。未知の名前は ``LookupError``。"""
    key = name.strip().lower()
    if key == "auto":
        return "auto"
    key = _ALIASES.get(key, key)
    codecs.lookup(key)
    return key


def detect_encoding(sample: bytes) -> str:
    if sample.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    decoder = codecs.getincrementaldecoder("utf-8")()
    try:
        # final=False: サンプル末尾で途切れたマルチバイト文字は許容する
        decoder.decode(sample, final=False)
        return "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        codecs.getincrementaldecoder("cp932")().decode(sample, final=False)
        return "cp932"
    except UnicodeDecodeError:
        return "utf-8"


def open_text(path: str | Path, encoding: str = "auto", errors: str = "strict") -> IO[str]:
    """テキストとして開く。``-`` は標準入力。``auto`` は先頭サンプルで判定する。"""
    enc = canonical_encoding(encoding)
    if str(path) == "-":
        raw = sys.stdin.buffer
        if enc == "auto":
            buffered = io.BufferedReader(raw, buffer_size=SAMPLE_BYTES)  # type: ignore[type-var]
            enc = detect_encoding(buffered.peek(SAMPLE_BYTES)[:SAMPLE_BYTES])
            return io.TextIOWrapper(buffered, encoding=enc, errors=errors, newline="")
        return io.TextIOWrapper(raw, encoding=enc, errors=errors, newline="")
    if enc == "auto":
        with open(path, "rb") as fh:
            enc = detect_encoding(fh.read(SAMPLE_BYTES))
    return open(path, encoding=enc, errors=errors, newline="")
