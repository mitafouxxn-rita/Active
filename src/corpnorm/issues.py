"""データ品質の検出結果（不整合・警告）を表す型。

全ての検出結果は機械可読なコードと重大度を持ち、CSV 出力・JSONL エラーログ・
REST API レスポンスで同一の語彙として扱われる。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class IssueCode(str, Enum):
    # 法人名
    EMPTY_NAME = "EMPTY_NAME"
    NO_LEGAL_FORM = "NO_LEGAL_FORM"
    MULTIPLE_LEGAL_FORMS = "MULTIPLE_LEGAL_FORMS"
    ABBREVIATED_LEGAL_FORM = "ABBREVIATED_LEGAL_FORM"
    BANK_KANA_FORMAT = "BANK_KANA_FORMAT"
    ENGLISH_LEGAL_SUFFIX = "ENGLISH_LEGAL_SUFFIX"
    # 住所
    EMPTY_ADDRESS = "EMPTY_ADDRESS"
    PREFECTURE_MISSING = "PREFECTURE_MISSING"
    PREFECTURE_COMPLETED = "PREFECTURE_COMPLETED"
    CITY_NOT_DETECTED = "CITY_NOT_DETECTED"
    BLOCK_NUMBER_MISSING = "BLOCK_NUMBER_MISSING"
    POSTAL_CODE_MISSING = "POSTAL_CODE_MISSING"
    POSTAL_CODE_INVALID = "POSTAL_CODE_INVALID"
    POSTAL_CODE_UNKNOWN = "POSTAL_CODE_UNKNOWN"
    POSTAL_PREFECTURE_MISMATCH = "POSTAL_PREFECTURE_MISMATCH"
    POSTAL_CITY_MISMATCH = "POSTAL_CITY_MISMATCH"
    POSTAL_CODE_CONFLICT = "POSTAL_CODE_CONFLICT"
    # 法人番号
    CORPORATE_NUMBER_INVALID = "CORPORATE_NUMBER_INVALID"
    CORPORATE_NUMBER_NOT_FOUND = "CORPORATE_NUMBER_NOT_FOUND"
    CORPORATION_NOT_FOUND = "CORPORATION_NOT_FOUND"
    CORPORATION_AMBIGUOUS = "CORPORATION_AMBIGUOUS"
    CORPORATION_CLOSED = "CORPORATION_CLOSED"
    CORPORATE_NAME_CHANGED = "CORPORATE_NAME_CHANGED"
    ENRICHMENT_FAILED = "ENRICHMENT_FAILED"
    # レコード
    DUPLICATE_SUSPECTED = "DUPLICATE_SUSPECTED"
    MALFORMED_ROW = "MALFORMED_ROW"


#: コードごとの既定の重大度。
DEFAULT_SEVERITY: dict[IssueCode, Severity] = {
    IssueCode.EMPTY_NAME: Severity.ERROR,
    IssueCode.NO_LEGAL_FORM: Severity.INFO,
    IssueCode.MULTIPLE_LEGAL_FORMS: Severity.WARNING,
    IssueCode.ABBREVIATED_LEGAL_FORM: Severity.INFO,
    IssueCode.BANK_KANA_FORMAT: Severity.INFO,
    IssueCode.ENGLISH_LEGAL_SUFFIX: Severity.INFO,
    IssueCode.EMPTY_ADDRESS: Severity.WARNING,
    IssueCode.PREFECTURE_MISSING: Severity.ERROR,
    IssueCode.PREFECTURE_COMPLETED: Severity.INFO,
    IssueCode.CITY_NOT_DETECTED: Severity.WARNING,
    IssueCode.BLOCK_NUMBER_MISSING: Severity.WARNING,
    IssueCode.POSTAL_CODE_MISSING: Severity.INFO,
    IssueCode.POSTAL_CODE_INVALID: Severity.ERROR,
    IssueCode.POSTAL_CODE_UNKNOWN: Severity.WARNING,
    IssueCode.POSTAL_PREFECTURE_MISMATCH: Severity.ERROR,
    IssueCode.POSTAL_CITY_MISMATCH: Severity.ERROR,
    IssueCode.POSTAL_CODE_CONFLICT: Severity.WARNING,
    IssueCode.CORPORATE_NUMBER_INVALID: Severity.ERROR,
    IssueCode.CORPORATE_NUMBER_NOT_FOUND: Severity.ERROR,
    IssueCode.CORPORATION_NOT_FOUND: Severity.WARNING,
    IssueCode.CORPORATION_AMBIGUOUS: Severity.WARNING,
    IssueCode.CORPORATION_CLOSED: Severity.WARNING,
    IssueCode.CORPORATE_NAME_CHANGED: Severity.INFO,
    IssueCode.ENRICHMENT_FAILED: Severity.WARNING,
    IssueCode.DUPLICATE_SUSPECTED: Severity.WARNING,
    IssueCode.MALFORMED_ROW: Severity.ERROR,
}


@dataclass(frozen=True)
class Issue:
    code: IssueCode
    message: str
    severity: Severity
    field: str | None = None

    @classmethod
    def of(cls, code: IssueCode, message: str, field: str | None = None) -> Issue:
        return cls(code=code, message=message, severity=DEFAULT_SEVERITY[code], field=field)

    def to_dict(self) -> dict[str, str | None]:
        return {
            "code": self.code.value,
            "severity": self.severity.value,
            "field": self.field,
            "message": self.message,
        }


#: 健全性スコアの減点幅。
SEVERITY_PENALTY: dict[Severity, int] = {
    Severity.ERROR: 30,
    Severity.WARNING: 10,
    Severity.INFO: 0,
}


def health_score(issues: list[Issue] | tuple[Issue, ...]) -> int:
    """100 点満点のレコード健全性スコア。同一コードは一度だけ減点する。"""
    seen: set[IssueCode] = set()
    score = 100
    for issue in issues:
        if issue.code in seen:
            continue
        seen.add(issue.code)
        score -= SEVERITY_PENALTY[issue.severity]
    return max(score, 0)
