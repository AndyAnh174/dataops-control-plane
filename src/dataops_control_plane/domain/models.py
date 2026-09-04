from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, Column, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


class PlatformState(SQLModel, table=True):
    __tablename__ = "platform_state"

    id: int = Field(default=1, primary_key=True)
    bootstrap_completed_at: datetime


class AppUser(SQLModel, table=True):
    __tablename__ = "app_users"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    email: str = Field(unique=True, index=True, max_length=320)
    password_hash: str = Field(max_length=512)
    status: str = Field(default="ACTIVE", index=True, max_length=32)
    created_at: datetime


class Workspace(SQLModel, table=True):
    __tablename__ = "workspaces"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(max_length=120)
    created_by: UUID = Field(foreign_key="app_users.id", index=True)
    created_at: datetime


class WorkspaceMember(SQLModel, table=True):
    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "user_id",
            name="uq_workspace_member_identity",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workspace_id: UUID = Field(foreign_key="workspaces.id", index=True)
    user_id: UUID = Field(foreign_key="app_users.id", index=True)
    role: str = Field(index=True, max_length=32)
    created_at: datetime


class Project(SQLModel, table=True):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "provider",
            "project_ref",
            name="uq_project_provider_reference",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    workspace_id: UUID = Field(foreign_key="workspaces.id", index=True)
    name: str = Field(max_length=120)
    provider: str = Field(index=True, max_length=64)
    project_ref: str = Field(index=True, max_length=255)
    default_branch: str = Field(default="main", max_length=255)
    created_at: datetime


class IntegrationToken(SQLModel, table=True):
    __tablename__ = "integration_tokens"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "name",
            name="uq_integration_token_project_name",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    project_id: UUID = Field(foreign_key="projects.id", index=True)
    name: str = Field(max_length=120)
    token_prefix: str = Field(index=True, max_length=16)
    secret_hash: str = Field(unique=True, index=True, max_length=64)
    scopes: list[str] = Field(sa_column=Column(JSON, nullable=False))
    expires_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = Field(default=None, index=True)
    created_by: UUID = Field(foreign_key="app_users.id", index=True)
    created_at: datetime


class WebSession(SQLModel, table=True):
    __tablename__ = "web_sessions"

    token_hash: str = Field(primary_key=True, max_length=64)
    user_id: UUID = Field(foreign_key="app_users.id", index=True)
    created_at: datetime
    expires_at: datetime = Field(index=True)


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


class PipelineReport(SQLModel, table=True):
    __tablename__ = "pipeline_reports"
    __table_args__ = (
        UniqueConstraint(
            "pipeline_run_id",
            "report_type",
            "checksum",
            name="uq_pipeline_report_content",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    pipeline_run_id: UUID = Field(foreign_key="pipeline_runs.id", index=True)
    report_type: str = Field(index=True, max_length=64)
    source_uri: str = Field(max_length=2048)
    checksum: str = Field(max_length=64)
    payload: dict[str, object] = Field(sa_column=Column(JSON, nullable=False))
    redaction_count: int = Field(default=0, ge=0)
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


class Evidence(SQLModel, table=True):
    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint(
            "citation_id",
            name="uq_evidence_citation_id",
        ),
        UniqueConstraint(
            "incident_id",
            "evidence_type",
            "source_uri",
            "checksum",
            name="uq_evidence_source_content",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    citation_id: str = Field(index=True, max_length=64)
    incident_id: UUID = Field(foreign_key="incidents.id", index=True)
    evidence_type: str = Field(index=True, max_length=64)
    source_uri: str = Field(max_length=2048)
    checksum: str = Field(max_length=64)
    excerpt: str = Field(sa_column=Column(Text, nullable=False))
    details: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    collected_at: datetime


class RCAReport(SQLModel, table=True):
    __tablename__ = "rca_reports"
    __table_args__ = (
        UniqueConstraint(
            "incident_id",
            "input_checksum",
            "model_name",
            "prompt_version",
            name="uq_rca_report_reproducible_input",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    incident_id: UUID = Field(foreign_key="incidents.id", index=True)
    analysis_status: str = Field(max_length=32)
    incident_type: str = Field(index=True, max_length=64)
    root_cause: str = Field(sa_column=Column(Text, nullable=False))
    confidence: float
    evidence_claims: list[dict[str, object]] = Field(sa_column=Column(JSON, nullable=False))
    knowledge_document_ids: list[str] = Field(sa_column=Column(JSON, nullable=False))
    recommended_action: dict[str, object] = Field(sa_column=Column(JSON, nullable=False))
    missing_information: list[str] = Field(sa_column=Column(JSON, nullable=False))
    input_checksum: str = Field(index=True, max_length=64)
    model_name: str = Field(max_length=128)
    embedding_model: str = Field(max_length=128)
    prompt_version: str = Field(max_length=64)
    llm_calls: int = Field(default=1, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    graph_trace: list[str] = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime


class RecoveryPlan(SQLModel, table=True):
    __tablename__ = "recovery_plans"
    __table_args__ = (
        UniqueConstraint(
            "rca_report_id",
            "policy_version",
            name="uq_recovery_plan_policy_input",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    incident_id: UUID = Field(foreign_key="incidents.id", index=True)
    rca_report_id: UUID = Field(foreign_key="rca_reports.id", index=True)
    action_type: str = Field(index=True, max_length=64)
    parameters: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    risk_level: str = Field(index=True, max_length=32)
    policy_decision: str = Field(index=True, max_length=32)
    approval_status: str = Field(index=True, max_length=32)
    decision_reasons: list[str] = Field(sa_column=Column(JSON, nullable=False))
    policy_version: str = Field(max_length=64)
    approved_by: str | None = Field(default=None, max_length=255)
    decided_at: datetime | None = None
    created_at: datetime


class RecoveryAttempt(SQLModel, table=True):
    __tablename__ = "recovery_attempts"
    __table_args__ = (
        UniqueConstraint("plan_id", name="uq_recovery_attempt_plan"),
        UniqueConstraint("idempotency_key", name="uq_recovery_attempt_idempotency_key"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    incident_id: UUID = Field(foreign_key="incidents.id", index=True)
    plan_id: UUID = Field(foreign_key="recovery_plans.id", index=True)
    provider: str = Field(index=True, max_length=64)
    action_type: str = Field(index=True, max_length=64)
    attempt_number: int = Field(default=1, ge=1)
    status: str = Field(index=True, max_length=32)
    idempotency_key: str = Field(index=True, max_length=64)
    external_reference: str | None = Field(default=None, max_length=2048)
    result_details: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    verification_status: str | None = Field(default=None, index=True, max_length=32)
    verification_details: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    started_at: datetime
    finished_at: datetime | None = None


class RecoveryAuditEvent(SQLModel, table=True):
    __tablename__ = "recovery_audit_events"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    incident_id: UUID = Field(foreign_key="incidents.id", index=True)
    plan_id: UUID | None = Field(default=None, foreign_key="recovery_plans.id", index=True)
    attempt_id: UUID | None = Field(default=None, foreign_key="recovery_attempts.id", index=True)
    event_type: str = Field(index=True, max_length=64)
    actor: str = Field(max_length=255)
    details: dict[str, object] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    created_at: datetime
