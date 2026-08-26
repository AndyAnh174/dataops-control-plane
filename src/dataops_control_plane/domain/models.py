from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class PipelineRun(SQLModel, table=True):
    __tablename__ = "pipeline_runs"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "project_ref",
            "external_run_id",
            "attempt",
            name="uq_pipeline_run_external_identity",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    provider: str = Field(index=True, max_length=64)
    project_ref: str = Field(index=True, max_length=255)
    external_run_id: str = Field(index=True, max_length=255)
    attempt: int = Field(default=1, ge=1)
    commit_sha: str = Field(max_length=128)
    branch: str = Field(max_length=255)
    status: str = Field(max_length=32)
    failed_stage: str | None = Field(default=None, max_length=255)
    last_event_at: datetime


class ProcessedEvent(SQLModel, table=True):
    __tablename__ = "processed_events"

    event_id: str = Field(primary_key=True, max_length=255)
    pipeline_run_id: UUID = Field(foreign_key="pipeline_runs.id", index=True)
    received_at: datetime


class Incident(SQLModel, table=True):
    __tablename__ = "incidents"
    __table_args__ = (
        UniqueConstraint(
            "pipeline_run_id",
            name="uq_incident_pipeline_run_id",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    pipeline_run_id: UUID = Field(foreign_key="pipeline_runs.id", index=True)
    status: str = Field(default="OPEN", index=True, max_length=32)
    trigger_event_id: str = Field(foreign_key="processed_events.event_id", max_length=255)
    created_at: datetime
    updated_at: datetime
