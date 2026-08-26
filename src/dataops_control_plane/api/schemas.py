from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
