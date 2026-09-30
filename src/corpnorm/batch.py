"""大容量 CSV のストリーム処理。

* 入力は 1 行ずつ読み、``chunk_size`` 行単位で処理して即座に書き出す。
  常駐メモリは「チャンク 1 つ分の行」＋「重複判定用の短い比較キー」に限られる。
* 正規化・法人番号照合はスレッドプールで並列化し、チャンク内の順序は保持する。
* 出力は同じディレクトリの一時ファイルに書き、成功時にのみリネームする（原子的置換）。
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import sys
import tempfile
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from corpnorm.dedupe import DuplicateDetector
from corpnorm.encoding import canonical_encoding, open_text
from corpnorm.issues import Issue, IssueCode
from corpnorm.pipeline import InputRecord, ProcessedRecord, RecordProcessor, dedupe_record
from corpnorm.report import BatchReport

logger = logging.getLogger(__name__)

OUTPUT_COLUMNS: tuple[str, ...] = (
    "norm_name",
    "norm_name_core",
    "norm_legal_form",
    "norm_legal_form_position",
    "norm_branch",
    "norm_postal_code",
    "norm_prefecture",
    "norm_city",
    "norm_town",
    "norm_block",
    "norm_building",
    "norm_address",
    "corp_number",
    "corp_official_name",
    "corp_official_address",
    "corp_match_status",
    "corp_match_score",
    "dup_flag",
    "dup_of_row",
    "dup_score",
    "health_score",
    "issue_codes",
    "issue_messages",
)

#: 列名の自動推定候補（前にあるものを優先）
NAME_COLUMN_CANDIDATES = (
    "会社名", "企業名", "法人名", "商号", "社名", "取引先名", "顧客名", "屋号", "名称",
    "company_name", "company", "corporate_name", "name",
)  # fmt: skip
ADDRESS_COLUMN_CANDIDATES = (
    "住所", "所在地", "本店所在地", "本社所在地", "address", "addr",
)  # fmt: skip
POSTAL_COLUMN_CANDIDATES = ("郵便番号", "〒", "postal_code", "zip", "zipcode", "postcode")
NUMBER_COLUMN_CANDIDATES = ("法人番号", "corporate_number", "houjin_bangou")
ID_COLUMN_CANDIDATES = ("id", "ID", "顧客ID", "取引先ID", "顧客コード", "取引先コード")


class ColumnError(ValueError):
    pass


@dataclass(frozen=True)
class ColumnMapping:
    name: str
    address: str | None = None
    postal_code: str | None = None
    corporate_number: str | None = None
    id: str | None = None

    @classmethod
    def resolve(
        cls,
        header: list[str],
        *,
        name: str | None = None,
        address: str | None = None,
        postal_code: str | None = None,
        corporate_number: str | None = None,
        id: str | None = None,
    ) -> ColumnMapping:
        """明示指定を検証し、未指定の列はヘッダーから推定する。"""
        cols = set(header)

        def pick(explicit: str | None, candidates: tuple[str, ...], label: str) -> str | None:
            if explicit is not None:
                if explicit not in cols:
                    raise ColumnError(
                        f"{label}列「{explicit}」が見つかりません。ヘッダー: {', '.join(header)}"
                    )
                return explicit
            lowered = {h.strip().lower(): h for h in header}
            for cand in candidates:
                if cand.lower() in lowered:
                    return lowered[cand.lower()]
            return None

        name_col = pick(name, NAME_COLUMN_CANDIDATES, "法人名")
        if name_col is None:
            raise ColumnError(
                "法人名の列を特定できません。--name-col で指定してください。"
                f"ヘッダー: {', '.join(header)}"
            )
        return cls(
            name=name_col,
            address=pick(address, ADDRESS_COLUMN_CANDIDATES, "住所"),
            postal_code=pick(postal_code, POSTAL_COLUMN_CANDIDATES, "郵便番号"),
            corporate_number=pick(corporate_number, NUMBER_COLUMN_CANDIDATES, "法人番号"),
            id=pick(id, ID_COLUMN_CANDIDATES, "ID"),
        )

    def to_record(self, row: dict[str, str | None]) -> InputRecord:
        def get(col: str | None) -> str | None:
            return row.get(col) if col else None

        return InputRecord(
            name=get(self.name),
            address=get(self.address),
            postal_code=get(self.postal_code),
            corporate_number=get(self.corporate_number),
            record_id=get(self.id),
        )


@dataclass
class BatchOptions:
    encoding: str = "auto"
    output_encoding: str = "utf-8"
    delimiter: str = ","
    workers: int = 1
    chunk_size: int = 1000
    dedupe: bool = True
    dup_threshold: float = 0.88
    max_rows: int | None = None
    progress_every: int = 10_000


def output_values(rec: ProcessedRecord) -> list[str]:
    n, a, e, d = rec.name, rec.address, rec.enrichment, rec.duplicate
    corp = e.corporation if e else None

    def s(v: object) -> str:
        return "" if v is None else str(v)

    return [
        n.normalized,
        n.core,
        s(n.legal_form),
        s(n.legal_form_position),
        s(n.branch),
        s(a.postal_code) if a else "",
        s(a.prefecture) if a else "",
        s(a.city) if a else "",
        s(a.town) if a else "",
        s(a.block) if a else "",
        s(a.building) if a else "",
        a.normalized if a else "",
        s(rec.effective_corporate_number),
        corp.name if corp else "",
        corp.address if corp else "",
        e.status if e else "",
        s(e.score) if e and e.score is not None else "",
        "1" if d else "0",
        s(d.of_row) if d else "",
        s(d.score) if d else "",
        str(rec.health_score),
        ";".join(dict.fromkeys(i.code.value for i in rec.issues)),
        " / ".join(i.message for i in rec.issues if i.severity.value != "info"),
    ]


@contextmanager
def _atomic_output(path: str, encoding: str) -> Iterator[IO[str]]:
    if path == "-":
        buf = getattr(sys.stdout, "buffer", None)
        if buf is None:  # pragma: no cover - バイナリ層を持たない特殊な stdout
            yield sys.stdout
            return
        stream = io.TextIOWrapper(buf, encoding=encoding, newline="", write_through=True)
        try:
            yield stream
        finally:
            stream.flush()
            stream.detach()  # 標準出力自体は閉じない
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as fh:
            yield fh
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _chunks(
    reader: Iterator[dict[str | None, str | list[str] | None]], size: int, max_rows: int | None
) -> Iterator[list[dict[str | None, str | list[str] | None]]]:
    chunk: list[dict[str | None, str | list[str] | None]] = []
    for count, row in enumerate(reader):
        if max_rows is not None and count >= max_rows:
            break
        chunk.append(row)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def run_batch(
    input_path: str,
    output_path: str,
    processor: RecordProcessor,
    *,
    mapping: ColumnMapping | None = None,
    column_hints: dict[str, str | None] | None = None,
    options: BatchOptions | None = None,
    error_log: str | None = None,
    progress: Callable[[int], None] | None = None,
) -> BatchReport:
    opts = options or BatchOptions()
    if opts.workers < 1:
        raise ValueError("workers は 1 以上で指定してください")
    if opts.chunk_size < 1:
        raise ValueError("chunk_size は 1 以上で指定してください")
    out_enc = canonical_encoding(opts.output_encoding)
    if out_enc == "auto":
        raise ValueError("出力エンコーディングに auto は指定できません")

    report = BatchReport(input=input_path, output=output_path)
    detector = DuplicateDetector(opts.dup_threshold) if opts.dedupe else None

    with (
        open_text(input_path, opts.encoding) as src,
        _atomic_output(output_path, out_enc) as dst,
        open(error_log, "w", encoding="utf-8") if error_log else nullcontext() as elog,
    ):
        reader = csv.DictReader(src, delimiter=opts.delimiter)
        header = [h for h in (reader.fieldnames or []) if h is not None]
        if not header:
            raise ColumnError("入力 CSV にヘッダー行がありません")
        mapping = mapping or ColumnMapping.resolve(header, **(column_hints or {}))
        clash = [c for c in OUTPUT_COLUMNS if c in header]
        if clash:
            raise ColumnError(f"入力に出力列と同名の列があります: {', '.join(clash)}")
        report.extra["columns"] = {k: v for k, v in mapping.__dict__.items() if v is not None}
        writer = csv.writer(dst, delimiter=opts.delimiter, lineterminator="\n")
        writer.writerow([*header, *OUTPUT_COLUMNS])

        row_no = 0
        executor = ThreadPoolExecutor(max_workers=opts.workers) if opts.workers > 1 else None
        try:
            for chunk in _chunks(iter(reader), opts.chunk_size, opts.max_rows):
                inputs: list[tuple[InputRecord, bool]] = []
                for raw in chunk:
                    malformed = None in raw or any(raw.get(h) is None for h in header)
                    clean = {k: v for k, v in raw.items() if isinstance(k, str)}
                    inputs.append((mapping.to_record(clean), malformed))  # type: ignore[arg-type]
                if executor is not None:
                    results = list(executor.map(processor.process, [r for r, _ in inputs]))
                else:
                    results = [processor.process(r) for r, _ in inputs]

                for raw, (_, malformed), rec in zip(chunk, inputs, results, strict=True):
                    row_no += 1
                    if malformed:
                        rec.issues.append(
                            Issue.of(
                                IssueCode.MALFORMED_ROW,
                                "列数がヘッダーと一致しません",
                                None,
                            )
                        )
                    if detector is not None:
                        dedupe_record(detector, row_no, rec)
                    writer.writerow([_cell(raw.get(h)) for h in header] + output_values(rec))
                    report.add(row_no, rec)
                    if elog is not None:
                        for issue in rec.issues:
                            elog.write(
                                json.dumps(
                                    {
                                        "row": row_no,
                                        "id": rec.input.record_id,
                                        "input_name": rec.input.name,
                                        **issue.to_dict(),
                                    },
                                    ensure_ascii=False,
                                )
                                + "\n"
                            )
                    if progress is not None and row_no % opts.progress_every == 0:
                        progress(row_no)
                dst.flush()
        finally:
            if executor is not None:
                executor.shutdown(wait=True)

    return report.finish()


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ""
    return str(value)
