"""ドキュメントとコードの乖離を防ぐテスト。"""

import json
from pathlib import Path

from corpnorm.api.app import create_app
from corpnorm.api.settings import Settings
from corpnorm.issues import DEFAULT_SEVERITY, IssueCode

ROOT = Path(__file__).resolve().parent.parent


def test_every_issue_code_has_severity_and_doc() -> None:
    doc = (ROOT / "docs" / "issue-codes.md").read_text(encoding="utf-8")
    for code in IssueCode:
        assert code in DEFAULT_SEVERITY
        assert f"`{code.value}` | {DEFAULT_SEVERITY[code].value} |" in doc


def test_committed_openapi_is_current() -> None:
    committed = json.loads((ROOT / "docs" / "openapi.json").read_text(encoding="utf-8"))
    assert committed == create_app(Settings()).openapi(), (
        "docs/openapi.json が古いです。`make openapi` で再生成してください"
    )
