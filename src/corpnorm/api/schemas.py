"""API のリクエスト／レスポンス定義（OpenAPI スキーマの元になる）。"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from corpnorm.issues import IssueCode, Severity


class IssueModel(BaseModel):
    code: IssueCode = Field(description="検出事項コード")
    severity: Severity
    field: str | None = Field(
        None, description="対象フィールド（name / address / postal_code ...）"
    )
    message: str


class NameRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"name": "ｶ)ﾔﾏﾀﾞｼｮｳｼﾞ"}]})
    name: str = Field(..., max_length=500, description="法人名・屋号")


class NameResult(BaseModel):
    original: str
    normalized: str = Field(description="正規化後の商号（法人格は正式名称に展開）")
    core: str = Field(description="法人格を除いた名称")
    legal_form: str | None = Field(None, description="法人格（株式会社 など）")
    legal_form_position: str | None = Field(None, description="prefix=前株 / suffix=後株")
    branch: str | None = Field(None, description="支店・営業所などの付帯部分")
    english_suffix: str | None = None
    match_key: str = Field(description="名寄せ用の比較キー")
    issues: list[IssueModel]


class AddressRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"address": "千代田区霞が関三丁目1番1号", "postal_code": "100-8978"}]
        }
    )
    address: str = Field(..., max_length=1000)
    postal_code: str | None = Field(None, max_length=20)


class AddressResult(BaseModel):
    original: str
    normalized: str
    postal_code: str | None = Field(None, description="123-4567 形式")
    prefecture: str | None
    prefecture_code: str | None = Field(None, description="JIS X 0401 都道府県コード")
    city: str | None
    town: str | None
    block: str | None = Field(None, description="番地（1-2-3 形式）")
    building: str | None
    match_key: str
    issues: list[IssueModel]


class CorporationModel(BaseModel):
    corporate_number: str = Field(description="13 桁の法人番号")
    name: str = Field(description="正式商号")
    kind: str | None = None
    kind_label: str | None = None
    furigana: str | None = None
    en_name: str | None = None
    prefecture_name: str | None = None
    city_name: str | None = None
    street_number: str | None = None
    address: str = Field(description="本店所在地")
    post_code: str | None = None
    close_date: str | None = None
    close_cause: str | None = None
    successor_corporate_number: str | None = None
    change_date: str | None = None
    update_date: str | None = None
    latest: bool = True


class MatchStatus(str, Enum):
    matched = "matched"
    ambiguous = "ambiguous"
    not_found = "not_found"
    error = "error"
    skipped = "skipped"


class CandidateModel(BaseModel):
    corporate_number: str
    name: str
    address: str
    score: float


class EnrichmentResult(BaseModel):
    status: MatchStatus
    score: float | None = None
    corporation: CorporationModel | None = None
    candidates: list[CandidateModel] = []
    issues: list[IssueModel] = []


class RecordIn(BaseModel):
    id: str | None = Field(None, max_length=200, description="呼び出し元のレコード ID")
    name: str = Field(..., max_length=500)
    address: str | None = Field(None, max_length=1000)
    postal_code: str | None = Field(None, max_length=20)
    corporate_number: str | None = Field(None, max_length=20)


class BatchOptions(BaseModel):
    enrich: bool = Field(False, description="国税庁 法人番号 API で名寄せする")
    dedupe: bool = Field(True, description="リクエスト内の重複を判定する")
    duplicate_threshold: float = Field(0.88, ge=0.5, le=1.0)


class BatchRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "records": [
                        {"id": "C-001", "name": "（株）山田商事", "address": "大阪市北区梅田1-1-1"},
                        {
                            "id": "C-002",
                            "name": "山田商事株式会社",
                            "address": "大阪府大阪市北区梅田一丁目1番1号",
                        },
                    ],
                    "options": {"enrich": False, "dedupe": True},
                }
            ]
        }
    )
    records: list[RecordIn] = Field(..., min_length=1, max_length=10_000)
    options: BatchOptions = BatchOptions()


class DuplicateModel(BaseModel):
    of_index: int = Field(description="重複元のレコード位置（0 始まり）")
    of_id: str | None = None
    score: float
    kind: str = Field(description="corporate_number / exact / fuzzy")


class RecordResult(BaseModel):
    index: int
    id: str | None
    name: NameResult
    address: AddressResult | None
    corporate_number: str | None
    enrichment: EnrichmentResult | None
    duplicate: DuplicateModel | None
    health_score: int = Field(ge=0, le=100, description="データ健全性スコア（100 点満点）")
    issues: list[IssueModel]


class BatchSummary(BaseModel):
    total: int
    with_errors: int
    with_warnings: int
    duplicates: int
    average_health_score: float
    enrichment: dict[str, int]


class BatchResponse(BaseModel):
    results: list[RecordResult]
    summary: BatchSummary


class IssueCodeInfo(BaseModel):
    code: IssueCode
    severity: Severity


class HealthResponse(BaseModel):
    status: str
    version: str


class ReadinessResponse(BaseModel):
    status: str
    enrichment_available: bool
    postal_dictionary_entries: int


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: list[dict[str, object]] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
