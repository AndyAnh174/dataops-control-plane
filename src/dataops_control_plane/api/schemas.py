from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator


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
