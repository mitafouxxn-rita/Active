import csv
import json
from pathlib import Path

import httpx
import pytest

from corpnorm import __version__
from corpnorm.cli import EXIT_OK, EXIT_QUALITY, EXIT_RUNTIME, EXIT_USAGE, main
from tests.conftest import YAMADA, FakeHoujin

CSV = "会社名,住所,郵便番号\n(株)山田商事,大阪市北区梅田3-1-3,530-0001\n,東京都港区芝公園4-2-8,530-0001\n"


@pytest.fixture
def src(tmp_path: Path) -> Path:
    p = tmp_path / "in.csv"
    p.write_text(CSV, encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def _no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOUJIN_APP_ID", raising=False)


@pytest.fixture
def mock_api(monkeypatch: pytest.MonkeyPatch, fake_houjin: FakeHoujin) -> FakeHoujin:
    real = httpx.Client

    def client(*a: object, **kw: object) -> httpx.Client:
        kw.pop("transport", None)
        return real(transport=httpx.MockTransport(fake_houjin), **kw)  # type: ignore[arg-type]

    monkeypatch.setattr("corpnorm.houjin.httpx.Client", client)
    return fake_houjin


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as ei:
        main(["--version"])
    assert ei.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_help_lists_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    out = capsys.readouterr().out
    for cmd in ("process", "name", "address", "lookup", "serve", "openapi"):
        assert cmd in out
    with pytest.raises(SystemExit):
        main(["process", "--help"])
    out = capsys.readouterr().out
    assert "--dup-threshold" in out and "--max-error-rate" in out


def test_process_ok(src: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out, rep = tmp_path / "out.csv", tmp_path / "r.json"
    code = main(["process", str(src), "-o", str(out), "--report", str(rep)])
    assert code == EXIT_OK
    assert json.loads(rep.read_text(encoding="utf-8"))["rows_total"] == 2
    assert "処理行数" in capsys.readouterr().err
    with open(out, encoding="utf-8") as fh:
        assert next(csv.DictReader(fh))["norm_name"] == "株式会社山田商事"


def test_process_to_stdout(src: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["process", str(src), "-o", "-"]) == EXIT_OK
    captured = capsys.readouterr()
    assert "norm_name" in captured.out
    assert "処理行数" not in captured.err  # CSV と混ざらないようサマリー抑止


def test_quality_gates(src: Path, tmp_path: Path) -> None:
    out = str(tmp_path / "o.csv")
    assert main(["process", str(src), "-o", out, "-q", "--fail-on-error"]) == EXIT_QUALITY
    assert main(["process", str(src), "-o", out, "-q", "--max-error-rate", "0.4"]) == EXIT_QUALITY
    assert main(["process", str(src), "-o", out, "-q", "--max-error-rate", "0.5"]) == EXIT_OK


@pytest.mark.parametrize(
    "args",
    [
        ["process", "missing.csv", "-o", "o.csv"],
        ["process", "{src}", "-o", "{src}"],
        ["process", "{src}", "-o", "o.csv", "--name-col", "存在しない列"],
        ["process", "{src}", "-o", "o.csv", "--enrich"],
        ["process", "{src}", "-o", "o.csv", "--postal-dict", "missing.csv"],
        ["process", "{src}", "-o", "o.csv", "--enrich", "--app-id", "x", "--cache", "ftp://x"],
        ["lookup", "1180301018771"],
    ],
)
def test_usage_errors(
    args: list[str], src: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:  # fmt: skip
    monkeypatch.chdir(tmp_path)
    assert main([a.replace("{src}", str(src)) for a in args]) == EXIT_USAGE
    assert "corpnorm: error:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "args",
    [
        ["process", "in.csv", "-o", "o.csv", "--workers", "0"],
        ["process", "in.csv", "-o", "o.csv", "--workers", "x"],
        ["process", "in.csv", "-o", "o.csv", "--dup-threshold", "1.5"],
        ["process", "in.csv", "-o", "o.csv", "--dup-threshold", "0"],
        ["process", "in.csv", "-o", "o.csv", "--dup-threshold", "abc"],
        ["process", "in.csv", "-o", "o.csv", "--max-error-rate", "x"],
        ["process", "in.csv", "-o", "o.csv", "--encoding", "nope"],
        ["process", "in.csv", "-o", "o.csv", "--delimiter", "ab"],
        ["process", "in.csv", "-o", "o.csv", "--rate-limit", "0"],
        ["process", "in.csv", "-o", "o.csv", "--rate-limit", "fast"],
        ["process", "in.csv"],  # -o 必須
        [],  # サブコマンド必須
    ],
)
def test_argparse_validation(args: list[str]) -> None:
    with pytest.raises(SystemExit) as ei:
        main(args)
    assert ei.value.code == EXIT_USAGE


def test_bad_encoding_is_usage_error(tmp_path: Path) -> None:
    p = tmp_path / "in.csv"
    p.write_bytes("会社名\n(株)山田\n".encode("cp932"))
    assert (
        main(["process", str(p), "-o", str(tmp_path / "o.csv"), "--encoding", "utf-8"])
        == EXIT_USAGE
    )


def test_tab_delimiter_and_sjis_output(tmp_path: Path) -> None:
    p = tmp_path / "in.tsv"
    p.write_text("会社名\t住所\n(株)A\t千代田区丸の内1-1\n", encoding="utf-8")
    out = tmp_path / "o.tsv"
    code = main(
        ["process", str(p), "-o", str(out), "--delimiter", "tab", "--output-encoding", "sjis", "-q"]
    )
    assert code == EXIT_OK
    assert "株式会社A" in out.read_text(encoding="cp932")


def test_name_and_address(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["name", "ｶ)ﾔﾏﾀﾞ"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["normalized"] == "株式会社ヤマダ"
    assert main(["name", "(株)A", "(有)B"]) == EXIT_OK
    assert len(json.loads(capsys.readouterr().out)) == 2
    assert main(["address", "千代田区霞が関三丁目1番1号", "--postal-code", "100-8978"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["normalized"] == "東京都千代田区霞が関3-1-1"


def test_address_with_dictionary(capsys: pytest.CaptureFixture[str]) -> None:
    fix = Path(__file__).parent / "fixtures" / "ken_all_sample.csv"
    assert main(["address", "東京都港区芝公園4-2-8", "東京都千代田区霞が関3-1-1",
                 "--postal-dict", str(fix)]) == EXIT_OK  # fmt: skip
    assert len(json.loads(capsys.readouterr().out)) == 2


def test_process_with_enrichment(src: Path, tmp_path: Path, mock_api: FakeHoujin) -> None:
    out, rep = tmp_path / "o.csv", tmp_path / "r.json"
    code = main([
        "process", str(src), "-o", str(out), "--report", str(rep), "-q", "--enrich",
        "--app-id", "APP", "--cache", f"sqlite:///{tmp_path}/c.db", "--workers", "2",
        "--error-log", str(tmp_path / "e.jsonl"),
    ])  # fmt: skip
    assert code == EXIT_OK
    report = json.loads(rep.read_text(encoding="utf-8"))
    assert report["houjin_api"]["api_calls"] >= 1
    with open(out, encoding="utf-8") as fh:
        row = next(csv.DictReader(fh))
    assert row["corp_number"] == YAMADA["corporateNumber"]
    # 2 回目はキャッシュヒットで API を呼ばない
    before = len(mock_api.requests)
    main(["process", str(src), "-o", str(out), "-q", "--enrich", "--app-id", "APP",
          "--cache", f"sqlite:///{tmp_path}/c.db", "--report", str(rep)])  # fmt: skip
    assert len(mock_api.requests) == before
    assert json.loads(rep.read_text(encoding="utf-8"))["houjin_api"]["cache"]["misses"] == 0


def test_process_with_redis_cache(
    src: Path, tmp_path: Path, mock_api: FakeHoujin, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fakeredis

    monkeypatch.setattr(
        "redis.Redis.from_url", lambda url, **kw: fakeredis.FakeRedis(decode_responses=True)
    )
    code = main(["process", str(src), "-o", str(tmp_path / "o.csv"), "-q", "--enrich",
                 "--app-id", "APP", "--cache", "redis://localhost/0"])  # fmt: skip
    assert code == EXIT_OK


def test_lookup(mock_api: FakeHoujin, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["lookup", YAMADA["corporateNumber"], "--app-id", "APP"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["corporation"]["name"] == "株式会社山田商事"
    assert (
        main(["lookup", "(株)山田商事", "--address", "大阪市北区梅田3-1-3", "--app-id", "APP"])
        == EXIT_OK
    )
    capsys.readouterr()
    assert main(["lookup", "存在しない", "--app-id", "APP"]) == EXIT_RUNTIME


def test_openapi_export(tmp_path: Path) -> None:
    j, y = tmp_path / "o.json", tmp_path / "o.yaml"
    assert main(["openapi", "-o", str(j)]) == EXIT_OK
    assert json.loads(j.read_text(encoding="utf-8"))["openapi"].startswith("3.")
    assert main(["openapi", "-o", str(y)]) == EXIT_OK
    assert "/v1/records/normalize" in y.read_text(encoding="utf-8")


def test_openapi_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["openapi"]) == EXIT_OK
    assert '"openapi"' in capsys.readouterr().out


def test_runtime_error_exit_code(
    src: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: object, **k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("corpnorm.batch.run_batch", boom)
    assert main(["process", str(src), "-o", str(tmp_path / "o.csv")]) == EXIT_RUNTIME


def test_json_logging(src: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["--log-format", "json", "process", str(src), "-o", str(tmp_path / "o.csv"), "-q"])
    lines = [
        json.loads(line) for line in capsys.readouterr().err.splitlines() if line.startswith("{")
    ]
    done = [entry for entry in lines if entry["message"] == "batch completed"]
    assert done and done[0]["rows"] == 2 and done[0]["level"] == "info"
