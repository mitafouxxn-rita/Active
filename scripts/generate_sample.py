"""ベンチマーク・デモ用の表記揺れを含むダミー CSV を生成する。

    python scripts/generate_sample.py -n 100000 -o sample.csv [--encoding cp932]

生成される社名・住所は架空のもの。約 15% の行は既存行の表記揺れ（重複候補）になる。
"""

from __future__ import annotations

import argparse
import csv
import random

PREFS = [
    ("東京都", "千代田区", "丸の内", "100-0005"),
    ("東京都", "新宿区", "西新宿", "160-0023"),
    ("大阪府", "大阪市北区", "梅田", "530-0001"),
    ("愛知県", "名古屋市中村区", "名駅", "450-0002"),
    ("福岡県", "福岡市博多区", "博多駅前", "812-0011"),
    ("北海道", "札幌市中央区", "大通西", "060-0042"),
    ("神奈川県", "横浜市西区", "みなとみらい", "220-0012"),
    ("三重県", "四日市市", "諏訪町", "510-0085"),
]
WORDS = [
    "山田", "田中", "佐藤", "鈴木", "高橋", "青葉", "旭", "富士", "日本", "東洋", "大和",
    "北斗", "未来", "光", "中央", "総合", "テクノ", "システム", "ソリューションズ",
    "エンジニアリング",
]  # fmt: skip
KINDS = ["商事", "工業", "物産", "建設", "電機", "製作所", "サービス", "ホールディングス"]
KANJI = "〇一二三四五六七八九"


def company(rng: random.Random) -> str:
    return "".join(rng.sample(WORDS, 2)) + rng.choice(KINDS)


def variant_name(core: str, rng: random.Random) -> str:
    return rng.choice(
        [
            f"株式会社{core}", f"(株){core}", f"（株）{core}", f"㈱{core}", f"{core}株式会社",
            f"{core}(株)", f"{core}㈱", f"株式会社 {core}", f"有限会社{core}", f"(有){core}",
            f"{core}", f"合同会社{core}",
        ]
    )  # fmt: skip


def variant_address(
    pref: tuple[str, str, str, str], a: int, b: int, c: int, rng: random.Random
) -> str:
    p, city, town, _ = pref
    kan = lambda n: "".join(KANJI[int(d)] for d in str(n))  # noqa: E731
    return rng.choice(
        [
            f"{p}{city}{town}{a}-{b}-{c}",
            f"{p}{city}{town}{a}丁目{b}番{c}号",
            f"{city}{town}{kan(a)}丁目{b}番地{c}",
            f"{p}{city}{town}{chr(0xFF10 + a % 10)}－{b}－{c}",
            f"{p} {city} {town} {a}-{b}-{c} 第{rng.randint(1, 9)}ビル{rng.randint(1, 20)}F",
        ]
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-n", "--rows", type=int, default=10_000)
    ap.add_argument("-o", "--output", default="sample.csv")
    ap.add_argument("--encoding", default="utf-8")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    seen: list[tuple[str, tuple[str, str, str, str], int, int, int]] = []
    with open(args.output, "w", encoding=args.encoding, newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["取引先コード", "会社名", "住所", "郵便番号", "担当者"])
        for i in range(1, args.rows + 1):
            if seen and rng.random() < 0.15:
                core, pref, a, b, c = rng.choice(seen)
            else:
                core, pref = company(rng), rng.choice(PREFS)
                a, b, c = rng.randint(1, 9), rng.randint(1, 30), rng.randint(1, 20)
                if len(seen) < 50_000:
                    seen.append((core, pref, a, b, c))
            postal = pref[3] if rng.random() > 0.1 else rng.choice(PREFS)[3]
            w.writerow(
                [
                    f"C{i:07d}",
                    variant_name(core, rng),
                    variant_address(pref, a, b, c, rng),
                    postal if rng.random() > 0.05 else "",
                    "担当" + str(rng.randint(1, 99)),
                ]
            )


if __name__ == "__main__":
    main()
