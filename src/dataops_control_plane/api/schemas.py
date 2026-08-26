from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


class PipelineStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class PipelineEventCreate(BaseModel):
    event_id: str = Field(min_length=1, max_length=255)
    event_type: str = Field(min_length=1, max_length=100)
    occurred_at: datetime
    provider: str = Field(pattern=r"^[a-z][a-z0-9_-]*$", max_length=64)
    project_ref: str = Field(min_length=1, max_length=255)
    external_run_id: str = Field(min_length=1, max_length=255)
    attempt: int = Field(default=1, ge=1)
    commit_sha: str = Field(min_length=7, max_length=128)
    branch: str = Field(min_length=1, max_length=255)
    status: PipelineStatus
    failed_stage: str | None = Field(default=None, max_length=255)


class PipelineEventReceipt(BaseModel):
    event_id: str
    run_id: UUID
    duplicate: bool
    run_status: PipelineStatus


class PipelineRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider: str
    project_ref: str
    external_run_id: str
    attempt: int
    commit_sha: str
    branch: str
    status: PipelineStatus
    failed_stage: str | None
    last_event_at: datetime

    @field_validator("last_event_at", mode="before")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class DataQualityDimension(StrEnum):
    SCHEMA = "schema"
    COMPLETENESS = "completeness"
    UNIQUENESS = "uniqueness"
    VALIDITY = "validity"
    VOLUME = "volume"


class DataContractRef(BaseModel):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    version: str = Field(min_length=1, max_length=64)


class DataQualitySummary(BaseModel):
    checks: int = Field(ge=1, le=50)
    passed: int = Field(ge=0, le=50)
    failed: int = Field(ge=0, le=50)


class DataQualityCheck(BaseModel):
    id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9._-]*$")
    dimension: DataQualityDimension
    success: bool
    expectation: str = Field(min_length=1, max_length=255)
    expected: JsonValue
    observed: JsonValue


class DataQualityDataset(BaseModel):
    row_count: int = Field(ge=0)
    columns: list[str] = Field(min_length=1, max_length=100)

    @field_validator("columns")
    @classmethod
    def require_unique_columns(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("dataset columns must be unique")
        if any(not column or len(column) > 255 for column in value):
            raise ValueError("dataset columns must contain names between 1 and 255 characters")
        return value


class DataQualityReportCreate(BaseModel):
    schema_version: str = Field(pattern=r"^1\.[0-9]+$", max_length=16)
    contract: DataContractRef
    scenario: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    success: bool
    summary: DataQualitySummary
    checks: list[DataQualityCheck] = Field(min_length=1, max_length=50)
    dataset: DataQualityDataset
    generated_at: datetime

    @field_validator("generated_at")
    @classmethod
    def normalize_generated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def require_consistent_results(self) -> "DataQualityReportCreate":
        passed = sum(check.success for check in self.checks)
        failed = len(self.checks) - passed
        if len({check.id for check in self.checks}) != len(self.checks):
            raise ValueError("data quality check ids must be unique")
        if self.summary.checks != len(self.checks):
            raise ValueError("summary checks must equal the number of checks")
        if self.summary.passed != passed or self.summary.failed != failed:
            raise ValueError("summary pass/fail counts must match check results")
        if self.success != (failed == 0):
            raise ValueError("report success must match check results")
        return self


class DataQualityReportReceipt(BaseModel):
    report_id: UUID
    run_id: UUID
    checksum: str
    duplicate: bool


class IncidentStatus(StrEnum):
    OPEN = "OPEN"
    COLLECTING_EVIDENCE = "COLLECTING_EVIDENCE"
    ANALYZING = "ANALYZING"
    ACTION_REQUIRED = "ACTION_REQUIRED"
    RESOLVED = "RESOLVED"


class IncidentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: IncidentStatus
    trigger_event_id: str
    created_at: datetime
    updated_at: datetime
    pipeline_run: PipelineRunRead

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def normalize_timestamps(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class IncidentListResponse(BaseModel):
    items: list[IncidentRead]


class EvidenceType(StrEnum):
    PIPELINE_METADATA = "PIPELINE_METADATA"
    LOG_EXCERPT = "LOG_EXCERPT"
    COMMIT_DIFF = "COMMIT_DIFF"
    DATA_QUALITY_REPORT = "DATA_QUALITY_REPORT"
    ARTIFACT_MANIFEST = "ARTIFACT_MANIFEST"


class EvidenceRead(BaseModel):
    id: UUID
    incident_id: UUID
    citation_id: str
    evidence_type: EvidenceType
    source_uri: str
    checksum: str
    excerpt: str
    metadata: dict[str, JsonValue]
    collected_at: datetime

    @field_validator("collected_at", mode="before")
    @classmethod
    def normalize_collected_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class EvidenceListResponse(BaseModel):
    items: list[EvidenceRead]


class EvidenceCollectionWarning(BaseModel):
    source: str
    code: str
    message: str


class EvidenceCollectionReceipt(BaseModel):
    incident_id: UUID
    incident_status: IncidentStatus
    collected_count: int
    duplicate_count: int
    evidence_count: int
    warnings: list[EvidenceCollectionWarning]


class PipelineLogEntryCreate(BaseModel):
    occurred_at: datetime
    job_name: str = Field(min_length=1, max_length=255)
    stage: str = Field(min_length=1, max_length=255)
    level: str = Field(pattern=r"^(TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL)$")
    stream: str = Field(pattern=r"^(stdout|stderr)$")
    sequence: int = Field(ge=0)
    message: str = Field(min_length=1, max_length=100_000)
    stack_trace: str | None = Field(default=None, max_length=200_000)
    tags: list[str] = Field(default_factory=list, max_length=32)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def normalize_occurred_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @field_validator("level", mode="before")
    @classmethod
    def normalize_level(cls, value: object) -> object:
        if isinstance(value, str):
            return value.upper()
        return value


class PipelineLogBatchCreate(BaseModel):
    entries: list[PipelineLogEntryCreate] = Field(min_length=1, max_length=500)


class PipelineLogReceipt(BaseModel):
    run_id: UUID
    accepted_count: int
    duplicate_count: int
    redaction_count: int


class PipelineLogRead(BaseModel):
    occurred_at: datetime
    job_name: str
    stage: str
    level: str
    stream: str
    sequence: int
    message: str
    stack_trace: str | None = None
    tags: list[str]
    metadata: dict[str, JsonValue]
    redaction_count: int


class PipelineLogSearchResponse(BaseModel):
    items: list[PipelineLogRead]
