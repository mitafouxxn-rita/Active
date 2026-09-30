"""法人名・屋号の正規化。

主な処理:

1. 文字幅・記号の統一（``（株）`` → ``(株)``、``㈱`` → ``(株)``、``ｶ)`` → ``カ)`` など）
2. 法人格表記の検出と展開（略称・全銀カナ略語・正式名称）
3. 前株／後株（法人格の位置）の判定
4. 支店・営業所などの付帯部分の分離
5. 名寄せ用比較キーの生成
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from corpnorm.issues import Issue, IssueCode
from corpnorm.text import match_key, normalize_width


@dataclass(frozen=True)
class LegalForm:
    """法人格。``abbr`` は ``(株)`` 形式の略称、``kana`` は全銀協の口座名義略語。"""

    name: str
    abbr: tuple[str, ...] = ()
    kana: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()


LEGAL_FORMS: tuple[LegalForm, ...] = (
    LegalForm("株式会社", ("株",), ("カ",)),
    LegalForm("有限会社", ("有",), ("ユ",)),
    LegalForm("合同会社", ("同",), ("ド",)),
    LegalForm("合資会社", ("資",), ("シ",)),
    LegalForm("合名会社", ("名",), ("メ",)),
    LegalForm("相互会社", (), ("ソ",)),
    LegalForm("一般社団法人", ("一社", "一般社"), ("シヤ",)),
    LegalForm("一般財団法人", ("一財", "一般財"), ("ザイ",)),
    LegalForm("公益社団法人", ("公社",), ()),
    LegalForm("公益財団法人", ("公財",), ()),
    LegalForm("特定非営利活動法人", ("特非",), ("トクヒ",), ("NPO法人",)),
    LegalForm("医療法人社団", (), ()),
    LegalForm("医療法人財団", (), ()),
    LegalForm("医療法人", ("医",), ("イ",)),
    LegalForm("社会福祉法人", ("福",), ("フク",)),
    LegalForm("学校法人", ("学",), ("ガク",)),
    LegalForm("宗教法人", ("宗",), ("シユウ",)),
    LegalForm("弁護士法人", ("弁",), ()),
    LegalForm("税理士法人", ("税",), ()),
    LegalForm("監査法人", ("監",), ()),
    LegalForm("司法書士法人", (), ()),
    LegalForm("行政書士法人", (), ()),
    LegalForm("社会保険労務士法人", (), ()),
    LegalForm("特許業務法人", (), ()),
    LegalForm("地方独立行政法人", (), ()),
    LegalForm("独立行政法人", ("独",), ("ドク",)),
    LegalForm("国立大学法人", (), ()),
    LegalForm("農事組合法人", ("農",), ()),
)

_BY_NAME = {lf.name: lf for lf in LEGAL_FORMS}

# 正式名称・別名（長いものから照合して「医療法人社団」を「医療法人」より優先する）
_FULL_NAMES: list[tuple[str, LegalForm]] = sorted(
    [(lf.name, lf) for lf in LEGAL_FORMS] + [(a, lf) for lf in LEGAL_FORMS for a in lf.aliases],
    key=lambda t: -len(t[0]),
)
_ABBRS: list[tuple[str, LegalForm]] = sorted(
    [(a, lf) for lf in LEGAL_FORMS for a in lf.abbr], key=lambda t: -len(t[0])
)
_KANAS: list[tuple[str, LegalForm]] = sorted(
    [(k, lf) for lf in LEGAL_FORMS for k in lf.kana], key=lambda t: -len(t[0])
)

_FULL_ALT = "|".join(re.escape(n) for n, _ in _FULL_NAMES)
_ABBR_ALT = "|".join(re.escape(a) for a, _ in _ABBRS)
_KANA_ALT = "|".join(re.escape(k) for k, _ in _KANAS)

# 前置: 「株式会社ABC」「(株)ABC」「株)ABC」「カ)ABC」「(カ)ABC」
_PREFIX_FULL_RE = re.compile(rf"^(?P<lf>{_FULL_ALT})\s*(?P<rest>.+)$")
_PREFIX_ABBR_RE = re.compile(rf"^\(?(?P<lf>{_ABBR_ALT})\)\s*(?P<rest>.+)$")
_PREFIX_KANA_RE = re.compile(rf"^\(?(?P<lf>{_KANA_ALT})\)\s*(?P<rest>.+)$")
# 後置: 「ABC株式会社」「ABC(株)」「ABC(株」「ABC(カ)」「ABC(カ」
_SUFFIX_FULL_RE = re.compile(rf"^(?P<rest>.+?)\s*(?P<lf>{_FULL_ALT})$")
_SUFFIX_ABBR_RE = re.compile(rf"^(?P<rest>.+?)\s*\((?P<lf>{_ABBR_ALT})\)?$")
_SUFFIX_KANA_RE = re.compile(rf"^(?P<rest>.+?)\s*\((?P<lf>{_KANA_ALT})\)?$")

_BRANCH_WORDS = "本店|本社|支店|支社|営業所|事業所|出張所|工場|研究所|センター|店|事務所"
# 「ABC株式会社 東京支店」「株式会社ABC 大阪営業所」の付帯部分
_MID_FULL_RE = re.compile(rf"^(?P<pre>.*?)(?P<lf>{_FULL_ALT})(?P<post>.+?(?:{_BRANCH_WORDS}))$")
_TRAILING_BRANCH_RE = re.compile(rf"^(?P<core>.+?)\s+(?P<branch>\S+(?:{_BRANCH_WORDS}))$")

# 英文法人格（照合キーから除外する）
_EN_SUFFIX_RE = re.compile(
    r"[\s,]*(?P<sfx>co\.?\s*,?\s*ltd\.?|company\s+limited|kabushiki\s+kaisha|k\.\s?k\.|"
    r"inc\.?|incorporated|corporation|corp\.?|ltd\.?|limited|l\.?l\.?c\.?|g\.\s?k\.)$",
    re.IGNORECASE,
)
_EN_TO_JA = {
    "kabushikikaisha": "株式会社",
    "kk": "株式会社",
    "llc": "合同会社",
    "gk": "合同会社",
}

_WRAPPING_PUNCT = " \t-・,.、。"

# 「株式 会社」のように法人格の途中に空白が入った表記
_SPACED_LEGAL_RE = re.compile(
    "|".join(r"\s*".join(re.escape(c) for c in n) for n, _ in _FULL_NAMES if len(n) > 1)
)


@dataclass(frozen=True)
class NormalizedName:
    original: str
    normalized: str
    core: str
    legal_form: str | None
    legal_form_position: str | None  # "prefix" | "suffix" | None
    branch: str | None
    match_key: str
    english_suffix: str | None = None
    issues: tuple[Issue, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, object]:
        return {
            "original": self.original,
            "normalized": self.normalized,
            "core": self.core,
            "legal_form": self.legal_form,
            "legal_form_position": self.legal_form_position,
            "branch": self.branch,
            "english_suffix": self.english_suffix,
            "match_key": self.match_key,
            "issues": [i.to_dict() for i in self.issues],
        }


def _lookup(table: list[tuple[str, LegalForm]], token: str) -> LegalForm:
    for key, lf in table:
        if key == token:
            return lf
    raise KeyError(token)  # pragma: no cover - 正規表現と表が一致している限り到達しない


def _detect(
    s: str,
) -> tuple[LegalForm | None, str | None, str, str | None]:
    """(法人格, 位置, 残り, 検出形式) を返す。検出形式は full/abbr/kana。"""
    for regex, table, kind, pos in (
        (_PREFIX_FULL_RE, _FULL_NAMES, "full", "prefix"),
        (_SUFFIX_FULL_RE, _FULL_NAMES, "full", "suffix"),
        (_PREFIX_ABBR_RE, _ABBRS, "abbr", "prefix"),
        (_SUFFIX_ABBR_RE, _ABBRS, "abbr", "suffix"),
        (_PREFIX_KANA_RE, _KANAS, "kana", "prefix"),
        (_SUFFIX_KANA_RE, _KANAS, "kana", "suffix"),
    ):
        m = regex.match(s)
        if m and m.group("rest").strip(_WRAPPING_PUNCT):
            return _lookup(table, m.group("lf")), pos, m.group("rest"), kind
    return None, None, s, None


def normalize_name(raw: str | None) -> NormalizedName:
    """法人名・屋号を正規化する。

    >>> normalize_name("ｶ)ﾔﾏﾀﾞｼｮｳｼﾞ").normalized
    '株式会社ヤマダショウジ'
    >>> normalize_name("山田商事（株）").legal_form_position
    'suffix'
    """
    original = raw or ""
    issues: list[Issue] = []
    # 付帯部分を空白で区切れるよう、この段階では日本語間の空白を保持する
    s = _SPACED_LEGAL_RE.sub(
        lambda m: re.sub(r"\s", "", m.group(0)), normalize_width(original, keep_cjk_spaces=True)
    )
    if not s:
        return NormalizedName(
            original=original,
            normalized="",
            core="",
            legal_form=None,
            legal_form_position=None,
            branch=None,
            match_key="",
            issues=(Issue.of(IssueCode.EMPTY_NAME, "法人名が空です", "name"),),
        )

    # 支店名など法人格の後ろに続く付帯部分を切り出す
    branch: str | None = None
    m_mid = _MID_FULL_RE.match(s)
    if m_mid and not _SUFFIX_FULL_RE.match(s):
        s = f"{m_mid.group('pre')}{m_mid.group('lf')}"
        branch = m_mid.group("post").strip(_WRAPPING_PUNCT)
        if not m_mid.group("pre").strip():
            # 「株式会社ABC東京支店」は前株。支店語を含む単語を分離できないため core に残す
            s = f"{m_mid.group('lf')}{m_mid.group('post')}"
            branch = None

    lf, position, rest, kind = _detect(s)

    english_suffix: str | None = None
    if lf is None:
        m_en = _EN_SUFFIX_RE.search(s)
        if m_en and s[: m_en.start()].strip(_WRAPPING_PUNCT):
            english_suffix = m_en.group("sfx")
            rest = s[: m_en.start()]
            mapped = _EN_TO_JA.get(re.sub(r"[^a-z]", "", english_suffix.lower()))
            issues.append(
                Issue.of(
                    IssueCode.ENGLISH_LEGAL_SUFFIX,
                    f"英文の法人格表記「{english_suffix}」を検出しました",
                    "name",
                )
            )
            if mapped:
                lf = _BY_NAME[mapped]
                position = "suffix"

    core = rest.strip(_WRAPPING_PUNCT)

    # 付帯部分（全角スペース区切りの「東京支店」等）を core から分離
    if branch is None and lf is not None and position == "prefix":
        m_br = _TRAILING_BRANCH_RE.match(core)
        if m_br:
            core, branch = m_br.group("core"), m_br.group("branch")
    core = normalize_width(core)
    if branch is not None:
        branch = normalize_width(branch)

    # 二重の法人格（「株式会社ABC株式会社」「(株)ABC(有)」）
    lf2, _, rest2, _ = _detect(core)
    if lf2 is not None:
        issues.append(
            Issue.of(
                IssueCode.MULTIPLE_LEGAL_FORMS,
                f"法人格が複数含まれています（{lf.name if lf else ''}／{lf2.name}）",
                "name",
            )
        )
        core = rest2.strip(_WRAPPING_PUNCT)

    if lf is None:
        issues.append(
            Issue.of(
                IssueCode.NO_LEGAL_FORM,
                "法人格が見つかりません（個人事業主の屋号、または法人格の欠落）",
                "name",
            )
        )
    elif kind == "abbr":
        issues.append(
            Issue.of(IssueCode.ABBREVIATED_LEGAL_FORM, f"略称を「{lf.name}」に展開しました", "name")
        )
    elif kind == "kana":
        issues.append(
            Issue.of(
                IssueCode.BANK_KANA_FORMAT,
                f"全銀カナ略語を「{lf.name}」に展開しました（正式商号の確認を推奨）",
                "name",
            )
        )

    legal = lf.name if lf else None
    if legal and position == "prefix":
        normalized = f"{legal}{core}"
    elif legal and english_suffix is None:
        normalized = f"{core}{legal}"
    else:
        normalized = f"{core} {english_suffix}" if english_suffix else core

    return NormalizedName(
        original=original,
        normalized=normalized,
        core=core,
        legal_form=legal,
        legal_form_position=position if legal else None,
        branch=branch,
        match_key=match_key(core),
        english_suffix=english_suffix,
        issues=tuple(issues),
    )


def legal_form_names() -> list[str]:
    """サポートしている法人格の正式名称一覧。"""
    return [lf.name for lf in LEGAL_FORMS]
