"""1 レコード単位の処理パイプライン（CLI と REST API で共通）。

正規化（純粋関数・並列実行可）と重複判定（順序依存・逐次実行）を分離し、
前者のみをワーカースレッドに載せる。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from corpnorm.address import NormalizedAddress, normalize_address
from corpnorm.dedupe import DuplicateDetector, DuplicateMatch
from corpnorm.houjin import (
    CorporateMatcher,
    MatchResult,
    is_valid_corporate_number,
    normalize_corporate_number,
)
from corpnorm.issues import Issue, IssueCode, health_score
from corpnorm.name import NormalizedName, normalize_name
from corpnorm.postal import PostalDictionary


@dataclass(frozen=True)
class InputRecord:
    name: str | None
    address: str | None = None
    postal_code: str | None = None
    corporate_number: str | None = None
    record_id: str | None = None


@dataclass
class ProcessedRecord:
    input: InputRecord
    name: NormalizedName
    address: NormalizedAddress | None
    corporate_number: str | None
    enrichment: MatchResult | None
    issues: list[Issue] = field(default_factory=list)
    duplicate: DuplicateMatch | None = None

    @property
    def health_score(self) -> int:
        return health_score(self.issues)

    @property
    def effective_corporate_number(self) -> str | None:
        if self.enrichment and self.enrichment.corporation:
            return self.enrichment.corporation.corporate_number
        return self.corporate_number

    def attach_duplicate(self, match: DuplicateMatch | None) -> None:
        self.duplicate = match
        if match is None:
            return
        ref = f"ID {match.of_id}" if match.of_id else f"{match.of_row} 行目"
        self.issues.append(
            Issue.of(
                IssueCode.DUPLICATE_SUSPECTED,
                f"{ref} と重複の疑い（{match.kind}, 類似度 {match.score:.2f}）",
                None,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.input.record_id,
            "name": self.name.to_dict(),
            "address": self.address.to_dict() if self.address else None,
            "corporate_number": self.effective_corporate_number,
            "enrichment": self.enrichment.to_dict() if self.enrichment else None,
            "duplicate": (
                {
                    "of_row": self.duplicate.of_row,
                    "of_id": self.duplicate.of_id,
                    "score": self.duplicate.score,
                    "kind": self.duplicate.kind,
                }
                if self.duplicate
                else None
            ),
            "health_score": self.health_score,
            "issues": [i.to_dict() for i in self.issues],
        }


class RecordProcessor:
    def __init__(
        self,
        *,
        matcher: CorporateMatcher | None = None,
        postal_dict: PostalDictionary | None = None,
    ) -> None:
        self.matcher = matcher
        self.postal_dict = postal_dict

    def process(self, record: InputRecord) -> ProcessedRecord:
        name = normalize_name(record.name)
        address: NormalizedAddress | None = None
        if record.address is not None or record.postal_code is not None:
            address = normalize_address(
                record.address, record.postal_code, postal_dict=self.postal_dict
            )
        issues: list[Issue] = [*name.issues, *(address.issues if address else ())]

        number = normalize_corporate_number(record.corporate_number)
        if number is not None and not is_valid_corporate_number(number):
            issues.append(
                Issue.of(
                    IssueCode.CORPORATE_NUMBER_INVALID,
                    f"法人番号が不正です（13 桁・チェックデジット）: {record.corporate_number}",
                    "corporate_number",
                )
            )
            number = None

        enrichment: MatchResult | None = None
        if self.matcher is not None:
            if number is not None:
                enrichment = self.matcher.lookup_number(number)
            elif name.core:
                enrichment = self.matcher.match(name, address)
            if enrichment is not None:
                issues.extend(enrichment.issues)

        return ProcessedRecord(record, name, address, number, enrichment, issues)


def dedupe_record(detector: DuplicateDetector, row: int, rec: ProcessedRecord) -> None:
    """``rec`` を重複判定し、結果をレコードに反映する（逐次呼び出しが前提）。"""
    region = rec.address.prefecture or "" if rec.address else ""
    name = rec.name
    variant = f"{name.legal_form}:{name.legal_form_position}" if name.legal_form else ""
    match = detector.check_and_add(
        row,
        rec.name.match_key,
        rec.address.match_key if rec.address else "",
        region=region,
        corporate_number=rec.effective_corporate_number,
        record_id=rec.input.record_id,
        variant=variant,
    )
    rec.attach_duplicate(match)
