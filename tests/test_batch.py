import csv
import io
import json
from pathlib import Path

import pytest

from corpnorm.batch import (
    OUTPUT_COLUMNS,
    BatchOptions,
    ColumnError,
    ColumnMapping,
    run_batch,
)
from corpnorm.encoding import canonical_encoding, detect_encoding, open_text
from corpnorm.houjin import CorporateMatcher
from corpnorm.issues import Issue, IssueCode, Severity, health_score
from corpnorm.pipeline import InputRecord, RecordProcessor
from tests.conftest import YAMADA

HEADER = "取引先ID,会社名,住所,郵便番号\n"
ROWS = [
    "1,（株）山田商事,大阪市北区梅田三丁目1番3号,530-0001",
    "2,山田商事(株),大阪府大阪市北区梅田3-1-3,5300001",
    "3,ｶ)ﾔﾏﾀﾞｼｮｳｼﾞ,,",
    "4,,東京都港区芝公園4-2-8,530-0001",
]


def write(path: Path, text: str, encoding: str = "utf-8") -> Path:
    path.write_bytes(text.encode(encoding))
    return path


def read_out(path: Path, encoding: str = "utf-8") -> list[dict[str, str]]:
    with open(path, encoding=encoding, newline="") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture
def sample(tmp_path: Path) -> Path:
    return write(tmp_path / "in.csv", HEADER + "\n".join(ROWS) + "\n")


def test_end_to_end(sample: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.csv"
    elog = tmp_path / "e.jsonl"
    report = run_batch(str(sample), str(out), RecordProcessor(), error_log=str(elog))
    rows = read_out(out)
    assert len(rows) == 4
    assert list(rows[0].keys())[-len(OUTPUT_COLUMNS) :] == list(OUTPUT_COLUMNS)
    assert rows[0]["norm_name"] == "株式会社山田商事"
    assert rows[0]["norm_address"] == "大阪府大阪市北区梅田3-1-3"
    assert rows[1]["dup_flag"] == "1" and rows[1]["dup_of_row"] == "1"
    assert "EMPTY_NAME" in rows[3]["issue_codes"]
    assert "POSTAL_PREFECTURE_MISMATCH" in rows[3]["issue_codes"]
    assert int(rows[3]["health_score"]) < 50
    assert report.rows_total == 4 and report.rows_with_errors == 1 and report.duplicates == 1
    assert report.extra["columns"] == {
        "name": "会社名", "address": "住所", "postal_code": "郵便番号", "id": "取引先ID",
    }  # fmt: skip
    lines = [json.loads(line) for line in elog.read_text(encoding="utf-8").splitlines()]
    assert any(e["code"] == "EMPTY_NAME" and e["row"] == 4 for e in lines)
    d = report.to_dict()
    assert d["health"]["distribution"]["0-49"] == 1
    assert d["error_samples"][0]["row"] == 4
    assert "処理行数" in report.summary_text()


def test_shift_jis_input_and_excel_output(sample: Path, tmp_path: Path) -> None:
    sjis = write(tmp_path / "sjis.csv", sample.read_text(encoding="utf-8"), "cp932")
    out = tmp_path / "out.csv"
    run_batch(
        str(sjis), str(out), RecordProcessor(), options=BatchOptions(output_encoding="utf-8-sig")
    )
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")
    assert read_out(out, "utf-8-sig")[0]["norm_name"] == "株式会社山田商事"


def test_workers_preserve_order(tmp_path: Path) -> None:
    lines = [f"{i},株式会社テスト{i:05d},東京都千代田区丸の内1-{i}" for i in range(1, 501)]
    src = write(tmp_path / "in.csv", "id,会社名,住所\n" + "\n".join(lines) + "\n")
    out = tmp_path / "out.csv"
    opts = BatchOptions(workers=4, chunk_size=37)
    report = run_batch(str(src), str(out), RecordProcessor(), options=opts)
    rows = read_out(out)
    assert [r["id"] for r in rows] == [str(i) for i in range(1, 501)]
    assert report.rows_total == 500


def test_limit_and_no_dedupe(sample: Path, tmp_path: Path) -> None:
    out = tmp_path / "o.csv"
    r = run_batch(
        str(sample), str(out), RecordProcessor(), options=BatchOptions(max_rows=2, dedupe=False)
    )
    assert r.rows_total == 2
    assert all(row["dup_flag"] == "0" for row in read_out(out))


def test_explicit_columns_and_tsv(tmp_path: Path) -> None:
    src = write(tmp_path / "in.tsv", "code\tcompany\tloc\nA\t(有)田中\t千代田区丸の内1-1\n")
    out = tmp_path / "o.tsv"
    run_batch(
        str(src), str(out), RecordProcessor(),
        column_hints={"name": "company", "address": "loc", "id": "code"},
        options=BatchOptions(delimiter="\t"),
    )  # fmt: skip
    with open(out, encoding="utf-8") as fh:
        row = next(csv.DictReader(fh, delimiter="\t"))
    assert row["norm_name"] == "有限会社田中" and row["norm_prefecture"] == "東京都"


def test_malformed_rows(tmp_path: Path) -> None:
    src = write(tmp_path / "in.csv", "会社名,住所\n(株)A,東京都千代田区丸の内1-1,余分\n(株)B\n")
    out = tmp_path / "o.csv"
    r = run_batch(str(src), str(out), RecordProcessor())
    rows = read_out(out)
    assert all("MALFORMED_ROW" in row["issue_codes"] for row in rows)
    assert r.issues_by_code["MALFORMED_ROW"] == 2


def test_column_errors(tmp_path: Path) -> None:
    src = write(tmp_path / "in.csv", "foo,bar\n1,2\n")
    with pytest.raises(ColumnError, match="法人名の列"):
        run_batch(str(src), str(tmp_path / "o.csv"), RecordProcessor())
    with pytest.raises(ColumnError, match="見つかりません"):
        run_batch(
            str(src), str(tmp_path / "o.csv"), RecordProcessor(), column_hints={"name": "nope"}
        )
    empty = write(tmp_path / "empty.csv", "")
    with pytest.raises(ColumnError, match="ヘッダー"):
        run_batch(str(empty), str(tmp_path / "o.csv"), RecordProcessor())
    clash = write(tmp_path / "c.csv", "会社名,norm_name\nA,B\n")
    with pytest.raises(ColumnError, match="同名"):
        run_batch(str(clash), str(tmp_path / "o.csv"), RecordProcessor())
    # 失敗時は出力ファイルも一時ファイルも残らない
    assert not (tmp_path / "o.csv").exists()
    assert not list(tmp_path.glob(".o.csv.*"))


@pytest.mark.parametrize(
    "opts",
    [BatchOptions(workers=0), BatchOptions(chunk_size=0), BatchOptions(output_encoding="auto")],
)
def test_invalid_options(sample: Path, tmp_path: Path, opts: BatchOptions) -> None:
    with pytest.raises(ValueError):
        run_batch(str(sample), str(tmp_path / "o.csv"), RecordProcessor(), options=opts)


def test_column_mapping_candidates() -> None:
    m = ColumnMapping.resolve(["Company_Name", "Address", "ZIP", "法人番号"])
    assert (m.name, m.address, m.postal_code, m.corporate_number) == (
        "Company_Name", "Address", "ZIP", "法人番号",
    )  # fmt: skip


def test_progress_callback(tmp_path: Path) -> None:
    src = write(tmp_path / "in.csv", "会社名\n" + "\n".join(f"(株)A{i}" for i in range(25)) + "\n")
    seen: list[int] = []
    run_batch(
        str(src), str(tmp_path / "o.csv"), RecordProcessor(),
        options=BatchOptions(progress_every=10), progress=seen.append,
    )  # fmt: skip
    assert seen == [10, 20]


def test_enrichment_in_batch(tmp_path: Path, matcher: CorporateMatcher) -> None:
    src = write(
        tmp_path / "in.csv",
        "会社名,住所,法人番号\n"
        "(株)山田商事,大阪市北区梅田3-1-3,\n"
        f"なんでも,,{YAMADA['corporateNumber']}\n"
        "謎の会社,,1234567890123\n",
    )
    out = tmp_path / "o.csv"
    report = run_batch(str(src), str(out), RecordProcessor(matcher=matcher))
    rows = read_out(out)
    assert rows[0]["corp_number"] == YAMADA["corporateNumber"]
    assert rows[0]["corp_official_name"] == "株式会社山田商事"
    assert rows[0]["corp_match_status"] == "matched"
    assert rows[1]["corp_official_address"].startswith("大阪府")
    # 法人番号が一致するため 2 行目は 1 行目の重複
    assert rows[1]["dup_flag"] == "1"
    assert "CORPORATE_NUMBER_INVALID" in rows[2]["issue_codes"]
    assert report.enrichment["matched"] == 2


def test_pipeline_processor_without_address() -> None:
    rec = RecordProcessor().process(InputRecord(name="(株)ABC"))
    assert rec.address is None
    d = rec.to_dict()
    assert d["address"] is None and d["duplicate"] is None and d["health_score"] == 100


def test_health_score() -> None:
    e = Issue.of(IssueCode.EMPTY_NAME, "x")
    w = Issue.of(IssueCode.CITY_NOT_DETECTED, "x")
    i = Issue.of(IssueCode.NO_LEGAL_FORM, "x")
    assert health_score([]) == 100
    assert health_score([i]) == 100
    assert health_score([e, w]) == 60
    assert health_score([e, e]) == 70  # 同一コードは 1 回だけ減点
    assert health_score([Issue(c, "", Severity.ERROR) for c in list(IssueCode)[:5]]) == 0


# ---------------------------------------------------------------- encoding


def test_detect_encoding() -> None:
    assert detect_encoding("会社名".encode()) == "utf-8"
    assert detect_encoding(b"\xef\xbb\xbf" + "会社名".encode()) == "utf-8-sig"
    assert detect_encoding("会社名".encode("cp932")) == "cp932"
    # マルチバイト文字の途中で途切れたサンプルでも UTF-8 と判定する
    assert detect_encoding("会社名".encode()[:-1]) == "utf-8"
    assert detect_encoding(b"\x81 \x81 x") == "utf-8"  # どちらでも解釈できなければ UTF-8


def test_canonical_encoding() -> None:
    assert canonical_encoding("SJIS") == "cp932"
    assert canonical_encoding("Shift_JIS") == "cp932"
    assert canonical_encoding("auto") == "auto"
    with pytest.raises(LookupError):
        canonical_encoding("nope-enc")


def test_open_text_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeStdin:
        buffer = io.BytesIO("会社名\n(株)A\n".encode("cp932"))

    monkeypatch.setattr("sys.stdin", FakeStdin())
    with open_text("-", "auto") as fh:
        assert fh.read().startswith("会社名")
    FakeStdin.buffer = io.BytesIO("会社名\n".encode())
    with open_text("-", "utf-8") as fh:
        assert fh.read() == "会社名\n"
