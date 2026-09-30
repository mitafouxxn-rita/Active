# corpnorm — 法人データ正規化・名寄せエンジン

取引先マスタ・顧客リスト・営業リストに含まれる **法人名と住所の表記揺れを統一し、
国税庁の法人番号で名寄せ・補完する** ツールです。
大容量 CSV を処理する **CLI** と、SaaS・Web フォームから呼び出す **REST API** の 2 形態で提供します。

```text
入力                                   出力
─────────────────────────────────     ──────────────────────────────────────────────
（株）青葉システムズ                →  株式会社青葉システムズ        前株 / 略称を展開
青葉システムズ㈱                    →  青葉システムズ株式会社        後株 / 重複候補（1 行目, 0.95）
ｶ)ｱｵﾊﾞｼｽﾃﾑｽﾞ                        →  株式会社アオバシステムズ      全銀カナ略語を展開
千代田区丸の内１－９－１  〒1000005  →  東京都千代田区丸の内1-9-1     都道府県を補完
四日市市諏訪町一番五号              →  三重県四日市市諏訪町1-5       地名の「四日市」は保持
東京都港区芝公園4-2-8  〒530-0001   →  POSTAL_PREFECTURE_MISMATCH    郵便番号は大阪府
```

## 特長

| 領域 | 内容 |
|---|---|
| 法人名 | `(株)` `（株）` `㈱` `㍿` `株)` `(株` `ｶ)` `(ｶ` など 28 種の法人格の略称・全銀カナ略語を正式名称に展開。前株／後株の判定、支店・営業所の分離、`Co., Ltd.` `K.K.` `LLC` などの英文法人格、二重法人格の検出 |
| 住所 | 全角・半角、各種ハイフン、漢数字（位取り・桁並び）の統一、「1丁目2番地の3」→`1-2-3`、都道府県／市区町村／町域／番地／建物名への分解、都道府県の補完、郵便番号との整合性チェック |
| 名寄せ | 国税庁 法人番号公表サイト Web-API で正式商号・13 桁の法人番号・最新の本店所在地を特定。法人番号のチェックデジット検証、閉鎖法人・承継法人・商号変更の検出 |
| 品質 | 26 種の検出事項コード、行ごとの健全性スコア（0〜100）、Levenshtein 距離による重複候補、JSON レポートと JSONL 明細 |
| 運用 | ストリーム処理（100 万行でもメモリ一定）、SQLite / Redis キャッシュ、分散レート制限、指数バックオフ再試行、原子的なファイル出力、CI 向け品質ゲートと終了コード、JSON 構造化ログ |
| API | OpenAPI 3.1 定義書（[`docs/openapi.yaml`](docs/openapi.yaml)）、Swagger UI、API キー認証、リクエスト ID、統一エラー形式 |

## クイックスタート

```bash
pip install -e ".[api,redis,fast]"      # Python 3.10+

# CSV を一括処理（列名・文字コードは自動判定）
corpnorm process examples/customers.csv -o cleaned.csv --report report.json

# 1 件だけ確認
corpnorm name "ｶ)ﾔﾏﾀﾞｼｮｳｼﾞ"
corpnorm address "〒100-8978 千代田区霞が関三丁目1番1号"

# REST API（http://127.0.0.1:8000/docs に Swagger UI）
corpnorm serve
```

`examples/` に入力例（[`customers.csv`](examples/customers.csv)）と、その処理結果
（[`customers.normalized.csv`](examples/customers.normalized.csv)・[`report.json`](examples/report.json)・[`issues.jsonl`](examples/issues.jsonl)）があります。

## CLI

```text
corpnorm process INPUT -o OUTPUT [options]   CSV を一括処理
corpnorm name TEXT...                          法人名を正規化（JSON）
corpnorm address TEXT... [--postal-code]       住所を正規化（JSON）
corpnorm lookup {法人番号|法人名} [--address]  法人番号 API で検索
corpnorm serve [--host --port --workers]       REST API を起動
corpnorm openapi [-o docs/openapi.yaml]        OpenAPI 定義書を出力
```

### `process` の主なオプション

| オプション | 既定 | 説明 |
|---|---|---|
| `--name-col` `--address-col` `--postal-col` `--number-col` `--id-col` | 自動推定 | 列の指定。省略時は「会社名」「企業名」「住所」「所在地」「郵便番号」「法人番号」などから推定 |
| `--encoding` / `--output-encoding` | `auto` / `utf-8` | `cp932`（Shift_JIS）、`utf-8-sig`（Excel 向け BOM 付き）など |
| `--delimiter` | `,` | `tab` で TSV |
| `--report PATH` | — | 健全性レポート（JSON） |
| `--error-log PATH` | — | 検出事項の明細（JSON Lines、1 行 1 件） |
| `--dup-threshold` / `--no-dedupe` | `0.88` | 重複とみなす類似度 |
| `--postal-dict KEN_ALL.csv` | — | 日本郵便の郵便番号データで市区町村レベルまで検証 |
| `--enrich` `--app-id` | — | 国税庁 API で名寄せ（`HOUJIN_APP_ID` 環境変数でも可） |
| `--cache` | `memory://` | `sqlite:///.cache/houjin.db`、`redis://host:6379/0`、`none` |
| `--rate-limit` | `5` | 国税庁 API の呼び出し上限（回/秒） |
| `--workers` / `--chunk-size` | `1` / `1000` | 並列数（API 照会時に有効）／一度にメモリに載せる行数 |
| `--max-error-rate` / `--fail-on-error` | — | 品質ゲート。超過時は終了コード 3 |
| `--limit N` | — | 先頭 N 行だけ処理（お試し実行） |
| `-o -` / 入力 `-` | — | 標準入出力（パイプライン連携） |

引数はすべて検証され、不正な値は処理開始前に終了コード 2 で拒否されます（`corpnorm process --help`）。

**終了コード**: `0` 成功 / `1` 実行時エラー / `2` 引数・入力形式エラー / `3` 品質ゲート不合格

### 出力列

入力の全列の後ろに次の列を追加します（入力と同じ順序・同じ行数）。

| 列 | 内容 |
|---|---|
| `norm_name` `norm_name_core` `norm_legal_form` `norm_legal_form_position` `norm_branch` | 正規化後の商号、法人格を除いた名称、法人格、`prefix`（前株）/`suffix`（後株）、支店等 |
| `norm_postal_code` `norm_prefecture` `norm_city` `norm_town` `norm_block` `norm_building` `norm_address` | 住所の各要素と正規化後の住所 |
| `corp_number` `corp_official_name` `corp_official_address` `corp_match_status` `corp_match_score` | 法人番号照合の結果（`matched` / `ambiguous` / `not_found` / `error`） |
| `dup_flag` `dup_of_row` `dup_score` | 重複候補（参照先は何行目のデータか、類似度） |
| `health_score` `issue_codes` `issue_messages` | 健全性スコア、検出事項コード（`;` 区切り）、メッセージ |

検出事項コードの一覧は [`docs/issue-codes.md`](docs/issue-codes.md) を参照してください。

### CI／夜間バッチでの利用例

```bash
export HOUJIN_APP_ID=xxxxxxxx
corpnorm --log-format json process /data/customers_$(date +%F).csv \
  -o /data/out/customers.csv --output-encoding utf-8-sig \
  --report /data/out/report.json --error-log /data/out/issues.jsonl \
  --enrich --cache sqlite:///var/cache/corpnorm/houjin.db --workers 4 \
  --max-error-rate 0.05
```

SQLite キャッシュにより、翌日以降の再実行では変更のあった取引先だけが国税庁 API に問い合わせられます。

## REST API

| メソッド | パス | 説明 |
|---|---|---|
| `POST` | `/v1/normalize/name` | 法人名の正規化 |
| `POST` | `/v1/normalize/address` | 住所の正規化と郵便番号照合 |
| `POST` | `/v1/records/normalize` | 複数レコードの一括処理（正規化・名寄せ・リクエスト内の重複判定） |
| `GET` | `/v1/corporations/{法人番号}` | 法人番号から最新の法人情報 |
| `GET` | `/v1/meta/issue-codes` | 検出事項コードの一覧 |
| `GET` | `/healthz` `/readyz` | 死活監視・準備状況 |

```bash
curl -s localhost:8000/v1/records/normalize -H 'content-type: application/json' -d '{
  "records": [
    {"id": "C-1", "name": "（株）山田商事", "address": "大阪市北区梅田1-1-1"},
    {"id": "C-2", "name": "株式会社山田商事", "address": "大阪府大阪市北区梅田一丁目1番1号"}
  ],
  "options": {"enrich": false, "dedupe": true}
}'
```

```jsonc
{
  "results": [
    { "index": 0, "id": "C-1",
      "name": { "normalized": "株式会社山田商事", "legal_form": "株式会社", "legal_form_position": "prefix", ... },
      "address": { "normalized": "大阪府大阪市北区梅田1-1-1", "prefecture_code": "27", ... },
      "health_score": 100, "duplicate": null, ... },
    { "index": 1, "id": "C-2", ...,
      "duplicate": { "of_index": 0, "of_id": "C-1", "score": 1.0, "kind": "exact" } }
  ],
  "summary": { "total": 2, "with_errors": 0, "duplicates": 1, "average_health_score": 95.0, ... }
}
```

エラーはすべて `{"error": {"code", "message", "request_id", "details"}}` 形式で返ります
（401 認証、413 件数超過、422 入力検証、502 国税庁 API 障害、503 名寄せ無効）。

### 設定（環境変数）

| 変数 | 既定 | 説明 |
|---|---|---|
| `HOUJIN_APP_ID` | — | 国税庁 法人番号 Web-API のアプリケーション ID。未設定なら名寄せは無効（正規化は動作） |
| `CORPNORM_API_KEYS` | — | カンマ区切り。設定すると `X-API-Key` ヘッダーが必須 |
| `CORPNORM_CACHE_URL` | `memory://` | `redis://...` で複数ワーカー間でキャッシュとレート制限を共有 |
| `CORPNORM_RATE_LIMIT` | `5` | 国税庁 API の呼び出し上限（回/秒、全ワーカー合計） |
| `CORPNORM_MAX_BATCH` | `1000` | `/v1/records/normalize` の 1 リクエスト上限件数 |
| `CORPNORM_POSTAL_DICT` | — | KEN_ALL.csv のパス |
| `CORPNORM_CORS_ORIGINS` | — | CORS を許可するオリジン（カンマ区切り） |
| `CORPNORM_LOG_FORMAT` | `text` | `json` で 1 行 JSON の構造化ログ |

### Docker

```bash
docker compose up --build          # API + Redis
curl localhost:8000/healthz
docker run --rm -v "$PWD:/data" corpnorm:latest process /data/in.csv -o /data/out.csv
```

イメージは非 root ユーザーで動作し、ヘルスチェックを内蔵しています。

## 性能

`scripts/generate_sample.py` で生成した表記揺れ入りのダミーデータ（約 15% が重複候補）での実測値です。
ピークメモリは処理プロセスの最大 RSS。

| 行数 | 入力サイズ | 重複判定 | 処理時間 | スループット | ピークメモリ |
|---:|---:|---|---:|---:|---:|
| 100,000 | 10.5 MB | なし | 13.6 s | 7,300 行/s | **36 MB** |
| 1,000,000 | 104.7 MB | なし | 120 s | 8,300 行/s | **36 MB** |
| 100,000 | 10.5 MB | あり | 25.7 s | 3,900 行/s | 100 MB |
| 1,000,000 | 104.7 MB | あり | 547 s | 1,800 行/s | 586 MB |

* 正規化はストリーム処理のため、**行数が 10 倍になってもメモリは増えません**。
* 重複判定は過去の行を参照するため、行データの代わりに短い比較キーだけを索引に保持します
  （1 行あたり約 550 バイト）。数百万行を超える場合は都道府県ごとの分割実行か `--no-dedupe` を推奨します。
* `rapidfuzz`（`pip install 'corpnorm[fast]'`）があれば Levenshtein 計算が C++ 実装になります。
  結果は純 Python 実装と完全に一致します（CI で検証）。
* 法人番号 API 連携時の処理時間は、API のレート制限（既定 5 回/秒）とキャッシュヒット率で決まります。

計測環境: Linux コンテナ（Python 3.11）。再現は `make bench`。

## 品質保証

```bash
make check     # ruff（lint + format）/ mypy --strict / pytest（カバレッジ 90% 未満で失敗）
```

* テスト 300 件以上、行カバレッジ 98% 以上（分岐カバレッジ込み）。
* 表記揺れのエッジケース（前株／後株、全銀カナ、丸囲み文字、空白混入、英文法人格、二重法人格、
  「四日市市」「蒲郡市」「玉村町」「三番町」などの地名、漢数字の位取り、条丁目、郵便番号の不整合…）を
  パラメータ化テストで網羅しています。
* 国税庁 API は `httpx.MockTransport` による擬似サーバーで、再試行・キャッシュ・レート制限・
  XML 異常系まで検証しています。Redis は `fakeredis` で検証します。
* GitHub Actions で Python 3.10〜3.13、`rapidfuzz` 非導入環境、Docker イメージのスモークテスト、
  OpenAPI 定義書の更新漏れを検査します。

## ドキュメント

* [アーキテクチャと設計判断](docs/architecture.md) — ストリーム処理、重複判定のブロッキング、キャッシュとレート制限、マッチングの採点方法
* [検出事項コード一覧](docs/issue-codes.md)
* [OpenAPI 定義書](docs/openapi.yaml)（[JSON](docs/openapi.json)）

## 制約と前提

* 国税庁 法人番号公表サイト Web-API の利用には、国税庁への利用届出によるアプリケーション ID が必要です。
  本ツールは API の利用規約・レート制限の範囲内で使用してください。
* 全銀カナ（`ｶ)ﾔﾏﾀﾞｼｮｳｼﾞ`）は漢字の正式商号と文字列比較できないため、候補のフリガナと照合して採点します。
  ただし候補の検索自体は国税庁 API の名称検索に依存するため、カナのみの入力では候補が得られない場合があり、
  その場合は `CORPORATION_NOT_FOUND` として目視確認の対象になります。
* 組み込みの市区町村表は政令指定都市・県庁所在地・東京 23 区・主要都市のみです。
  全国の市区町村・郵便番号で厳密に検証する場合は `--postal-dict` で KEN_ALL.csv を指定してください。
