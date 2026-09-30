# 検出事項コード一覧

CSV の `issue_codes` 列、エラーログ（JSONL）、REST API の `issues[].code` で共通の語彙です。
`GET /v1/meta/issue-codes` でも取得できます。

健全性スコアは 100 点から、コードごとに一度だけ次の点数を減点します: error −30、warning −10、info −0。

| コード | 重大度 | 意味 |
|---|---|---|
| `EMPTY_NAME` | error | 法人名が空 |
| `NO_LEGAL_FORM` | info | 法人格が見つからない（個人事業主の屋号、または法人格の欠落） |
| `MULTIPLE_LEGAL_FORMS` | warning | 法人格が二重に含まれる（例: 株式会社ABC株式会社） |
| `ABBREVIATED_LEGAL_FORM` | info | (株)・㈱ などの略称を正式名称に展開した |
| `BANK_KANA_FORMAT` | info | 全銀協カナ略語（ｶ) ﾕ) など）を展開した。正式商号の確認を推奨 |
| `ENGLISH_LEGAL_SUFFIX` | info | Co., Ltd. / Inc. などの英文法人格を検出 |
| `EMPTY_ADDRESS` | warning | 住所が空 |
| `PREFECTURE_MISSING` | error | 都道府県を特定できない |
| `PREFECTURE_COMPLETED` | info | 都道府県を市区町村名・郵便番号から補完した |
| `CITY_NOT_DETECTED` | warning | 市区町村を特定できない |
| `BLOCK_NUMBER_MISSING` | warning | 番地がない |
| `POSTAL_CODE_MISSING` | info | 郵便番号がない |
| `POSTAL_CODE_INVALID` | error | 郵便番号が 7 桁でない |
| `POSTAL_CODE_UNKNOWN` | warning | 郵便番号が辞書（KEN_ALL）に存在しない |
| `POSTAL_PREFECTURE_MISMATCH` | error | 郵便番号と住所の都道府県が一致しない |
| `POSTAL_CITY_MISMATCH` | error | 郵便番号と住所の市区町村が一致しない（辞書指定時） |
| `POSTAL_CODE_CONFLICT` | warning | 郵便番号欄と住所文字列中の郵便番号が異なる |
| `CORPORATE_NUMBER_INVALID` | error | 法人番号が 13 桁でない、またはチェックデジット不一致 |
| `CORPORATE_NUMBER_NOT_FOUND` | error | 法人番号が公表サイトに存在しない |
| `CORPORATION_NOT_FOUND` | warning | 名称検索で該当法人がない |
| `CORPORATION_AMBIGUOUS` | warning | 候補が複数あり一意に特定できない |
| `CORPORATION_CLOSED` | warning | 法人が閉鎖済み（承継法人があれば併記） |
| `CORPORATE_NAME_CHANGED` | info | 正式商号と入力値が異なる（商号変更・表記違い） |
| `ENRICHMENT_FAILED` | warning | 法人番号 API の照会に失敗（再試行後） |
| `DUPLICATE_SUSPECTED` | warning | 前の行と重複の疑い |
| `MALFORMED_ROW` | error | 列数がヘッダーと一致しない |
