"""コマンドラインインターフェース。

終了コード:
    0  正常終了
    1  実行時エラー（入出力・外部 API など）
    2  引数・入力形式のエラー
    3  品質ゲート不合格（--max-error-rate / --fail-on-error）
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from corpnorm import __version__
from corpnorm.logging_setup import configure_logging

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_USAGE = 2
EXIT_QUALITY = 3

logger = logging.getLogger("corpnorm")

_EPILOG = """\
例:
  # 列名を自動推定して正規化・重複判定（Shift_JIS も自動判定）
  corpnorm process customers.csv -o cleaned.csv --report report.json

  # 法人番号 API で名寄せ（SQLite キャッシュで再実行を高速化）
  export HOUJIN_APP_ID=xxxxxxxx
  corpnorm process customers.csv -o cleaned.csv --enrich \\
      --cache sqlite:///.cache/houjin.db --rate-limit 5 --workers 4

  # CI での品質ゲート（エラー行が 5%% を超えたら終了コード 3）
  corpnorm process in.csv -o out.csv --max-error-rate 0.05

  # 単発の確認
  corpnorm name "ｶ)ﾔﾏﾀﾞｼｮｳｼﾞ"
  corpnorm address "〒100-8978 千代田区霞が関三丁目1番1号"

終了コード: 0=成功 1=実行時エラー 2=引数エラー 3=品質ゲート不合格
"""


# ---------------------------------------------------------------- 引数型


def _positive_int(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"整数を指定してください: {value!r}") from None
    if n < 1:
        raise argparse.ArgumentTypeError(f"1 以上を指定してください: {value}")
    return n


def _positive_float(value: str) -> float:
    try:
        f = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"数値を指定してください: {value!r}") from None
    if f <= 0:
        raise argparse.ArgumentTypeError(f"正の数を指定してください: {value}")
    return f


def _ratio(value: str) -> float:
    try:
        f = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"数値を指定してください: {value!r}") from None
    if not 0.0 <= f <= 1.0:
        raise argparse.ArgumentTypeError(f"0〜1 の範囲で指定してください: {value}")
    return f


def _threshold(value: str) -> float:
    f = _ratio(value)
    if f == 0.0:
        raise argparse.ArgumentTypeError("0 より大きい値を指定してください")
    return f


def _encoding(value: str) -> str:
    from corpnorm.encoding import canonical_encoding

    try:
        return canonical_encoding(value)
    except LookupError:
        raise argparse.ArgumentTypeError(f"未知の文字コードです: {value!r}") from None


def _delimiter(value: str) -> str:
    mapping = {"\\t": "\t", "tab": "\t", "comma": ",", "semicolon": ";", "pipe": "|"}
    v = mapping.get(value, value)
    if len(v) != 1:
        raise argparse.ArgumentTypeError("区切り文字は 1 文字で指定してください")
    return v


# ---------------------------------------------------------------- パーサー


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="corpnorm",
        description="法人名・住所データの正規化／名寄せ／法人番号エンリッチメント",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--log-level",
        default=os.environ.get("CORPNORM_LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="ログレベル（既定: INFO, 環境変数 CORPNORM_LOG_LEVEL）",
    )
    parser.add_argument(
        "--log-format",
        default=os.environ.get("CORPNORM_LOG_FORMAT", "text"),
        choices=["text", "json"],
        help="ログ形式。json は集中ログ基盤向けの 1 行 JSON（既定: text）",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    # -- process
    p = sub.add_parser(
        "process",
        help="CSV を一括処理して整形済み CSV とレポートを出力",
        description=(
            "CSV をストリーム処理し、正規化列・法人番号・重複フラグ・健全性スコアを付与する。"
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("input", help="入力 CSV のパス（- で標準入力）")
    p.add_argument("-o", "--output", required=True, help="出力 CSV のパス（- で標準出力）")

    g = p.add_argument_group("列の指定（省略時はヘッダーから自動推定）")
    g.add_argument("--name-col", help="法人名の列（例: 会社名）")
    g.add_argument("--address-col", help="住所の列")
    g.add_argument("--postal-col", help="郵便番号の列")
    g.add_argument("--number-col", help="法人番号の列")
    g.add_argument("--id-col", help="レコード ID の列（重複の参照先表示に使用）")

    g = p.add_argument_group("入出力形式")
    g.add_argument(
        "--encoding",
        type=_encoding,
        default="auto",
        help="入力の文字コード（auto / utf-8 / cp932 ...）",
    )
    g.add_argument(
        "--output-encoding",
        type=_encoding,
        default="utf-8",
        help="出力の文字コード（Excel 向けは utf-8-sig か cp932）",
    )
    g.add_argument("--delimiter", type=_delimiter, default=",", help="区切り文字（tab 可）")
    g.add_argument("--report", metavar="PATH", help="健全性レポート（JSON）の出力先")
    g.add_argument("--error-log", metavar="PATH", help="検出事項の明細（JSON Lines）の出力先")
    g.add_argument("--limit", type=_positive_int, metavar="N", help="先頭 N 行のみ処理")

    g = p.add_argument_group("重複判定")
    g.add_argument("--no-dedupe", dest="dedupe", action="store_false", help="重複判定を行わない")
    g.add_argument(
        "--dup-threshold", type=_threshold, default=0.88, help="重複とみなす類似度（0〜1）"
    )

    g = p.add_argument_group("住所辞書")
    g.add_argument(
        "--postal-dict",
        metavar="KEN_ALL.csv",
        default=os.environ.get("CORPNORM_POSTAL_DICT"),
        help="日本郵便 KEN_ALL.csv。指定すると市区町村レベルで整合性を検証",
    )

    g = p.add_argument_group("法人番号 API 連携")
    _add_enrich_args(g)
    g.add_argument("--enrich", action="store_true", help="国税庁 法人番号 API で名寄せする")

    g = p.add_argument_group("性能")
    g.add_argument(
        "--workers", type=_positive_int, default=1, help="並列ワーカー数（API 照会時に有効）"
    )
    g.add_argument(
        "--chunk-size", type=_positive_int, default=1000, help="一度にメモリに載せる行数"
    )

    g = p.add_argument_group("品質ゲート（CI 向け）")
    g.add_argument(
        "--max-error-rate",
        type=_ratio,
        metavar="RATE",
        help="エラー行の割合がこれを超えたら終了コード 3",
    )
    g.add_argument(
        "--fail-on-error", action="store_true", help="エラー行が 1 件でもあれば終了コード 3"
    )
    g.add_argument("-q", "--quiet", action="store_true", help="サマリーを表示しない")

    # -- name / address
    p = sub.add_parser("name", help="法人名を正規化して JSON で表示")
    p.add_argument("text", nargs="+", help="法人名（複数可）")

    p = sub.add_parser("address", help="住所を正規化して JSON で表示")
    p.add_argument("text", nargs="+", help="住所（複数可）")
    p.add_argument("--postal-code", help="郵便番号（整合性チェック用）")
    p.add_argument(
        "--postal-dict", metavar="KEN_ALL.csv", default=os.environ.get("CORPNORM_POSTAL_DICT")
    )

    # -- lookup
    p = sub.add_parser(
        "lookup",
        help="法人番号 API で法人を検索",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("query", help="13 桁の法人番号、または法人名")
    p.add_argument("--address", help="名称検索時の所在地（照合精度の向上）")
    _add_enrich_args(p)

    # -- serve
    p = sub.add_parser(
        "serve",
        help="REST API サーバーを起動",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--host", default=os.environ.get("CORPNORM_HOST", "127.0.0.1"))
    p.add_argument("--port", type=_positive_int, default=int(os.environ.get("PORT", "8000")))
    p.add_argument("--workers", type=_positive_int, default=1, help="uvicorn ワーカー数")

    # -- openapi
    p = sub.add_parser("openapi", help="OpenAPI 定義書を出力")
    p.add_argument("-o", "--output", default="-", help="出力先（.yaml / .json、- で標準出力）")

    return parser


def _add_enrich_args(g: Any) -> None:
    g.add_argument(
        "--app-id",
        default=os.environ.get("HOUJIN_APP_ID"),
        help="国税庁 API のアプリケーション ID（環境変数 HOUJIN_APP_ID）",
    )
    g.add_argument(
        "--api-base-url",
        default=os.environ.get("HOUJIN_API_BASE_URL"),
        help="API のベース URL（検証環境・モック向け）",
    )
    g.add_argument(
        "--cache",
        default=os.environ.get("CORPNORM_CACHE_URL", "memory://"),
        help="キャッシュ URL（memory:// / sqlite:///path.db / redis://host/0 / none）",
    )
    g.add_argument(
        "--cache-ttl", type=_positive_int, default=7 * 24 * 3600, help="キャッシュ有効期間（秒）"
    )
    g.add_argument(
        "--rate-limit",
        type=_positive_float,
        default=float(os.environ.get("CORPNORM_RATE_LIMIT", "5")),
        help="API 呼び出しの上限（回/秒）",
    )


# ---------------------------------------------------------------- 実行


class UsageError(Exception):
    pass


def _make_matcher(args: argparse.Namespace) -> Any:
    from corpnorm.cache import RedisCache, create_cache
    from corpnorm.houjin import DEFAULT_BASE_URL, CorporateMatcher, HoujinClient
    from corpnorm.ratelimit import RateLimiter, RedisFixedWindowLimiter, TokenBucket

    if not args.app_id:
        raise UsageError(
            "法人番号 API の利用にはアプリケーション ID が必要です"
            "（--app-id または環境変数 HOUJIN_APP_ID）"
        )
    try:
        cache = create_cache(args.cache)
    except ValueError as exc:
        raise UsageError(str(exc)) from None
    limiter: RateLimiter
    if isinstance(cache, RedisCache):
        limiter = RedisFixedWindowLimiter(cache.client, max(1, int(args.rate_limit)))
    else:
        limiter = TokenBucket(args.rate_limit)
    client = HoujinClient(
        args.app_id,
        base_url=args.api_base_url or DEFAULT_BASE_URL,
        cache=cache,
        rate_limiter=limiter,
        cache_ttl=args.cache_ttl,
    )
    return CorporateMatcher(client)


def _load_postal_dict(path: str | None) -> Any:
    if not path:
        return None
    from corpnorm.postal import PostalDictionary

    if not Path(path).is_file():
        raise UsageError(f"郵便番号辞書が見つかりません: {path}")
    logger.info("loading postal dictionary: %s", path)
    d = PostalDictionary.load(path)
    logger.info("postal dictionary loaded: %d entries", len(d))
    return d


def cmd_process(args: argparse.Namespace) -> int:
    from corpnorm.batch import BatchOptions, ColumnError, run_batch
    from corpnorm.pipeline import RecordProcessor

    if args.input != "-" and not Path(args.input).is_file():
        raise UsageError(f"入力ファイルが見つかりません: {args.input}")
    if (
        args.input != "-"
        and args.output != "-"
        and (Path(args.input).resolve() == Path(args.output).resolve())
    ):
        raise UsageError("入力と出力に同じファイルは指定できません")
    if args.output == "-" and not args.quiet:
        args.quiet = True  # 標準出力を CSV が占有するためサマリーは抑止

    matcher = _make_matcher(args) if args.enrich else None
    processor = RecordProcessor(matcher=matcher, postal_dict=_load_postal_dict(args.postal_dict))
    options = BatchOptions(
        encoding=args.encoding,
        output_encoding=args.output_encoding,
        delimiter=args.delimiter,
        workers=args.workers,
        chunk_size=args.chunk_size,
        dedupe=args.dedupe,
        dup_threshold=args.dup_threshold,
        max_rows=args.limit,
    )
    hints = {
        "name": args.name_col,
        "address": args.address_col,
        "postal_code": args.postal_col,
        "corporate_number": args.number_col,
        "id": args.id_col,
    }
    try:
        report = run_batch(
            args.input,
            args.output,
            processor,
            column_hints=hints,
            options=options,
            error_log=args.error_log,
            progress=lambda n: logger.info("processed %s rows", f"{n:,}"),
        )
    except ColumnError as exc:
        raise UsageError(str(exc)) from None
    except UnicodeDecodeError as exc:
        raise UsageError(
            f"入力の文字コードを解釈できません（--encoding を指定してください）: {exc}"
        ) from None
    finally:
        if matcher is not None:
            report_extra = {
                "api_calls": matcher.client.api_calls,
                "cache": matcher.client.cache_stats.to_dict(),
            }
            matcher.client.close()
            matcher.client.cache.close()
    if matcher is not None:
        report.extra["houjin_api"] = report_extra

    if args.report:
        report.write(args.report)
    logger.info(
        "batch completed",
        extra={
            "rows": report.rows_total,
            "errors": report.rows_with_errors,
            "elapsed": round(report.elapsed_seconds, 3),
        },
    )
    if not args.quiet:
        print(report.summary_text(), file=sys.stderr)

    if args.fail_on_error and report.rows_with_errors > 0:
        logger.error("quality gate failed: %d error rows", report.rows_with_errors)
        return EXIT_QUALITY
    if args.max_error_rate is not None and report.error_rate > args.max_error_rate:
        logger.error(
            "quality gate failed: error rate %.4f > %.4f", report.error_rate, args.max_error_rate
        )
        return EXIT_QUALITY
    return EXIT_OK


def _print_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_name(args: argparse.Namespace) -> int:
    from corpnorm.name import normalize_name

    results = [normalize_name(t).to_dict() for t in args.text]
    _print_json(results[0] if len(results) == 1 else results)
    return EXIT_OK


def cmd_address(args: argparse.Namespace) -> int:
    from corpnorm.address import normalize_address

    pd = _load_postal_dict(args.postal_dict)
    results = [normalize_address(t, args.postal_code, postal_dict=pd).to_dict() for t in args.text]
    _print_json(results[0] if len(results) == 1 else results)
    return EXIT_OK


def cmd_lookup(args: argparse.Namespace) -> int:
    from corpnorm.address import normalize_address
    from corpnorm.houjin import normalize_corporate_number
    from corpnorm.name import normalize_name

    matcher = _make_matcher(args)
    try:
        digits = normalize_corporate_number(args.query)
        if digits is not None and len(digits) == 13 and digits == args.query.strip():
            result = matcher.lookup_number(digits)
        else:
            addr = normalize_address(args.address) if args.address else None
            result = matcher.match(normalize_name(args.query), addr)
    finally:
        matcher.client.close()
    _print_json(result.to_dict(max_candidates=10))
    return EXIT_OK if result.status == "matched" else EXIT_RUNTIME


def cmd_serve(args: argparse.Namespace) -> int:  # pragma: no cover - サーバー起動
    try:
        import uvicorn
    except ImportError:
        raise UsageError(
            "API サーバーには追加依存が必要です: pip install 'corpnorm[api]'"
        ) from None
    uvicorn.run(
        "corpnorm.api.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        workers=args.workers,
        log_config=None,
    )
    return EXIT_OK


def cmd_openapi(args: argparse.Namespace) -> int:
    from corpnorm.api.app import create_app

    spec = create_app().openapi()
    if args.output.endswith((".yaml", ".yml")):
        try:
            import yaml  # type: ignore[import-untyped,unused-ignore]
        except ImportError:
            raise UsageError(
                "YAML 出力には pyyaml が必要です: pip install 'corpnorm[yaml]'"
            ) from None
        text = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False)
    else:
        text = json.dumps(spec, ensure_ascii=False, indent=2) + "\n"
    if args.output == "-":
        sys.stdout.write(text)
    else:
        Path(args.output).write_text(text, encoding="utf-8")
    return EXIT_OK


COMMANDS = {
    "process": cmd_process,
    "name": cmd_name,
    "address": cmd_address,
    "lookup": cmd_lookup,
    "serve": cmd_serve,
    "openapi": cmd_openapi,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.log_level, args.log_format)
    try:
        return COMMANDS[args.command](args)
    except UsageError as exc:
        parser.print_usage(sys.stderr)
        print(f"corpnorm: error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:  # pragma: no cover
        print("中断しました", file=sys.stderr)
        return 130
    except Exception as exc:
        logger.exception("unexpected error: %s", exc)
        return EXIT_RUNTIME


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
