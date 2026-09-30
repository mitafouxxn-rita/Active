"""処理結果のデータ健全性レポート（集計のみ保持し、行データは保持しない）。"""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from corpnorm.issues import Severity
from corpnorm.pipeline import ProcessedRecord

_BUCKETS = ((90, "90-100"), (70, "70-89"), (50, "50-69"), (0, "0-49"))
_MAX_SAMPLES = 50


@dataclass
class BatchReport:
    input: str = ""
    output: str = ""
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    finished_at: str | None = None
    rows_total: int = 0
    rows_with_errors: int = 0
    rows_with_warnings: int = 0
    duplicates: int = 0
    score_sum: int = 0
    issues_by_code: Counter[str] = field(default_factory=Counter)
    issues_by_severity: Counter[str] = field(default_factory=Counter)
    health_distribution: Counter[str] = field(default_factory=Counter)
    enrichment: Counter[str] = field(default_factory=Counter)
    legal_forms: Counter[str] = field(default_factory=Counter)
    samples: list[dict[str, Any]] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    _t0: float = field(default_factory=time.perf_counter, repr=False)
    _elapsed: float | None = field(default=None, repr=False)

    def add(self, row: int, rec: ProcessedRecord) -> None:
        self.rows_total += 1
        score = rec.health_score
        self.score_sum += score
        for threshold, label in _BUCKETS:
            if score >= threshold:
                self.health_distribution[label] += 1
                break
        severities = {i.severity for i in rec.issues}
        if Severity.ERROR in severities:
            self.rows_with_errors += 1
        if Severity.WARNING in severities:
            self.rows_with_warnings += 1
        for issue in rec.issues:
            self.issues_by_code[issue.code.value] += 1
            self.issues_by_severity[issue.severity.value] += 1
            if issue.severity is Severity.ERROR and len(self.samples) < _MAX_SAMPLES:
                self.samples.append({"row": row, **issue.to_dict()})
        if rec.duplicate is not None:
            self.duplicates += 1
        if rec.enrichment is not None:
            self.enrichment[rec.enrichment.status] += 1
        self.legal_forms[rec.name.legal_form or "(なし)"] += 1

    def finish(self) -> BatchReport:
        self.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._elapsed = time.perf_counter() - self._t0
        return self

    @property
    def elapsed_seconds(self) -> float:
        return self._elapsed if self._elapsed is not None else time.perf_counter() - self._t0

    @property
    def error_rate(self) -> float:
        return self.rows_with_errors / self.rows_total if self.rows_total else 0.0

    @property
    def average_health_score(self) -> float:
        return round(self.score_sum / self.rows_total, 2) if self.rows_total else 100.0

    def to_dict(self) -> dict[str, Any]:
        elapsed = self.elapsed_seconds
        return {
            "input": self.input,
            "output": self.output,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_seconds": round(elapsed, 3),
            "rows_per_second": round(self.rows_total / elapsed, 1) if elapsed > 0 else None,
            "rows_total": self.rows_total,
            "rows_with_errors": self.rows_with_errors,
            "rows_with_warnings": self.rows_with_warnings,
            "error_rate": round(self.error_rate, 4),
            "duplicates": self.duplicates,
            "health": {
                "average_score": self.average_health_score,
                "distribution": {label: self.health_distribution[label] for _, label in _BUCKETS},
            },
            "issues_by_severity": dict(self.issues_by_severity),
            "issues_by_code": dict(self.issues_by_code.most_common()),
            "legal_forms": dict(self.legal_forms.most_common()),
            "enrichment": dict(self.enrichment),
            "error_samples": self.samples,
            **self.extra,
        }

    def write(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def summary_text(self) -> str:
        lines = [
            f"処理行数        : {self.rows_total:,}",
            f"エラー行        : {self.rows_with_errors:,} ({self.error_rate:.1%})",
            f"警告行          : {self.rows_with_warnings:,}",
            f"重複候補        : {self.duplicates:,}",
            f"平均健全性スコア: {self.average_health_score}",
            f"処理時間        : {self.elapsed_seconds:.2f}s",
        ]
        if self.enrichment:
            lines.append(
                "法人番号照合    : "
                + ", ".join(f"{k}={v:,}" for k, v in sorted(self.enrichment.items()))
            )
        top = self.issues_by_code.most_common(5)
        if top:
            lines.append("主な検出事項    : " + ", ".join(f"{k}={v:,}" for k, v in top))
        return "\n".join(lines)
