from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from dataops_control_plane.domain.models import Incident, PipelineRun, ProcessedEvent, RCAReport
from dataops_control_plane.main import create_app
from dataops_control_plane.services.recovery_execution import RecoveryExecutorError

RUN_ID = UUID("11000000-0000-0000-0000-000000000001")
INCIDENT_ID = UUID("22000000-0000-0000-0000-000000000001")
RCA_ID = UUID("33000000-0000-0000-0000-000000000001")


class EmptyLogStore:
    def close(self) -> None:
        pass


class FakeDispatch:
    def __init__(self, external_reference: str) -> None:
        self.external_reference = external_reference
        self.details = {"workflow": "dataops-recovery.yml"}


class FakeRecoveryExecutor:
    provider = "github"
    capabilities = frozenset({"RETRY", "QUARANTINE", "ROLLBACK_IMAGE"})

    def __init__(self) -> None:
        self.calls: list[object] = []

    def execute(self, request) -> FakeDispatch:
        self.calls.append(request)
        return FakeDispatch("github:workflow-dispatch:123456789")

    def close(self) -> None:
        pass


class FailingRecoveryExecutor(FakeRecoveryExecutor):
    def execute(self, request) -> FakeDispatch:
        self.calls.append(request)
        raise RecoveryExecutorError("GitHub recovery dispatch failed (HTTP 503)")


def _engine(*, enforce_foreign_keys: bool = False):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    if enforce_foreign_keys:

        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    SQLModel.metadata.create_all(engine)
    return engine


def _seed_validated_rca(
    engine,
    *,
    recommended_action: dict[str, object] | None = None,
) -> None:
    timestamp = datetime(2026, 9, 4, 8, 30, tzinfo=UTC)
    run = PipelineRun(
        id=RUN_ID,
        provider="github",
        project_ref="AndyAnh174/dataops-demo-app",
        external_run_id="987654321",
        attempt=1,
        commit_sha="e1437099e3aa3b0cd7b9c9db337081a55599fbb8",
        branch="main",
        status="FAILED",
        failed_stage="data-quality",
        last_event_at=timestamp,
    )
    event = ProcessedEvent(
        event_id="github:recovery-run:failed",
        pipeline_run_id=run.id,
        received_at=timestamp,
    )
    incident = Incident(
        id=INCIDENT_ID,
        pipeline_run_id=run.id,
        status="ACTION_REQUIRED",
        trigger_event_id=event.event_id,
        created_at=timestamp,
        updated_at=timestamp,
    )
    report = RCAReport(
        id=RCA_ID,
        incident_id=incident.id,
        analysis_status="VALIDATED",
        incident_type="DATA_QUALITY_VALIDITY",
        root_cause="Rows outside the amount contract caused the pipeline to fail.",
        confidence=0.93,
        evidence_claims=[
            {
                "citation_id": "EVD-DATA-QUALITY-001",
                "claim": "Two invalid rows exceeded the accepted amount range.",
            }
        ],
        knowledge_document_ids=["a" * 64],
        recommended_action=recommended_action
        or {
            "type": "QUARANTINE",
            "rationale": "Keep invalid rows out of trusted output.",
            "parameters": {"scope": "invalid_rows"},
            "requires_human_approval": True,
        },
        missing_information=[],
        input_checksum="b" * 64,
        model_name="test-gemma",
        embedding_model="test-bge-m3",
        prompt_version="rca-v1",
        llm_calls=1,
        prompt_tokens=500,
        completion_tokens=100,
        duration_ms=800,
        graph_trace=["load_context", "validate"],
        created_at=timestamp,
    )
    with Session(engine) as session:
        session.add(run)
        session.flush()
        session.add(event)
        session.flush()
        session.add(incident)
        session.flush()
        session.add(report)
        session.commit()


@pytest.fixture
def recovery_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATAOPS_AGENT_TOKEN", "recovery-test-token")
    engine = _engine()
    _seed_validated_rca(engine)
    with TestClient(
        create_app(
            engine=engine,
            log_store=EmptyLogStore(),
            evidence_sources=[],
        )
    ) as client:
        yield client


@pytest.fixture
def foreign_key_recovery_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATAOPS_AGENT_TOKEN", "recovery-test-token")
    engine = _engine(enforce_foreign_keys=True)
    _seed_validated_rca(engine)
    with TestClient(
        create_app(
            engine=engine,
            log_store=EmptyLogStore(),
            evidence_sources=[],
        ),
        raise_server_exceptions=False,
    ) as client:
        yield client


@pytest.fixture
def recovery_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, FakeRecoveryExecutor]]:
    monkeypatch.setenv("DATAOPS_AGENT_TOKEN", "recovery-test-token")
    engine = _engine()
    _seed_validated_rca(engine)
    executor = FakeRecoveryExecutor()
    application = create_app(
        engine=engine,
        log_store=EmptyLogStore(),
        evidence_sources=[],
    )
    application.state.recovery_executor = executor
    with TestClient(application) as client:
        yield client, executor


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer recovery-test-token"}


def test_create_recovery_plan_is_policy_driven_authenticated_and_idempotent(
    recovery_client: TestClient,
) -> None:
    """Catches LLM recommendations bypassing deterministic policy or creating duplicate plans."""
    missing_auth = recovery_client.post(f"/api/v1/incidents/{INCIDENT_ID}/recovery-plans")
    first = recovery_client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/recovery-plans",
        headers=_auth(),
    )
    repeated = recovery_client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/recovery-plans",
        headers=_auth(),
    )

    assert missing_auth.status_code == 401
    assert first.status_code == 201
    assert first.json()["incident_id"] == str(INCIDENT_ID)
    assert first.json()["rca_report_id"] == str(RCA_ID)
    assert first.json()["action_type"] == "QUARANTINE"
    assert first.json()["parameters"] == {"scope": "invalid_rows"}
    assert first.json()["risk_level"] == "MEDIUM"
    assert first.json()["policy_decision"] == "REQUIRE_APPROVAL"
    assert first.json()["approval_status"] == "PENDING"
    assert first.json()["policy_version"] == "recovery-v1"
    assert first.json()["duplicate"] is False
    assert repeated.status_code == 201
    assert repeated.json()["id"] == first.json()["id"]
    assert repeated.json()["duplicate"] is True


def test_create_recovery_plan_persists_plan_before_audit_foreign_key(
    foreign_key_recovery_client: TestClient,
) -> None:
    """Catches an audit insert racing ahead of its recovery plan foreign key."""
    created = foreign_key_recovery_client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/recovery-plans",
        headers=_auth(),
    )

    assert created.status_code == 201
    audit = foreign_key_recovery_client.get(
        f"/api/v1/incidents/{INCIDENT_ID}/recovery-audit",
        headers=_auth(),
    )
    assert [item["event_type"] for item in audit.json()["items"]] == ["PLAN_CREATED"]


def test_approved_plan_cannot_be_rejected_after_the_decision(
    recovery_client: TestClient,
) -> None:
    """Catches contradictory approval state that could make execution non-auditable."""
    created = recovery_client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/recovery-plans",
        headers=_auth(),
    )
    plan_id = created.json()["id"]

    approved = recovery_client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/recovery-plans/{plan_id}/approve",
        headers=_auth(),
        json={"actor": "demo-operator"},
    )
    rejected_after_approval = recovery_client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/recovery-plans/{plan_id}/reject",
        headers=_auth(),
        json={"actor": "demo-operator", "reason": "Changed my mind"},
    )

    assert approved.status_code == 200
    assert approved.json()["approval_status"] == "APPROVED"
    assert approved.json()["approved_by"] == "demo-operator"
    assert rejected_after_approval.status_code == 409
    assert rejected_after_approval.json() == {"detail": "Recovery plan has already been decided"}


def test_execution_requires_approval_and_is_idempotent(
    recovery_runtime: tuple[TestClient, FakeRecoveryExecutor],
) -> None:
    """Catches an unapproved or repeated API call dispatching a mutating action."""
    client, executor = recovery_runtime
    created = client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/recovery-plans",
        headers=_auth(),
    )
    plan_id = created.json()["id"]

    before_approval = client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/recovery-plans/{plan_id}/execute",
        headers=_auth(),
    )
    client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/recovery-plans/{plan_id}/approve",
        headers=_auth(),
        json={"actor": "demo-operator"},
    )
    first = client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/recovery-plans/{plan_id}/execute",
        headers=_auth(),
    )
    repeated = client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/recovery-plans/{plan_id}/execute",
        headers=_auth(),
    )

    assert before_approval.status_code == 409
    assert before_approval.json() == {"detail": "Recovery plan is not approved"}
    assert first.status_code == 202
    assert first.json()["plan_id"] == plan_id
    assert first.json()["status"] == "DISPATCHED"
    assert first.json()["provider"] == "github"
    assert first.json()["external_reference"] == "github:workflow-dispatch:123456789"
    assert first.json()["duplicate"] is False
    assert repeated.status_code == 202
    assert repeated.json()["id"] == first.json()["id"]
    assert repeated.json()["idempotency_key"] == first.json()["idempotency_key"]
    assert repeated.json()["duplicate"] is True
    assert len(executor.calls) == 1


def test_recovery_audit_records_policy_approval_and_dispatch(
    recovery_runtime: tuple[TestClient, FakeRecoveryExecutor],
) -> None:
    """Catches recovery mutations that cannot be reconstructed for a postmortem."""
    client, _ = recovery_runtime
    created = client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/recovery-plans",
        headers=_auth(),
    )
    plan_id = created.json()["id"]
    client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/recovery-plans/{plan_id}/approve",
        headers=_auth(),
        json={"actor": "demo-operator"},
    )
    client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/recovery-plans/{plan_id}/execute",
        headers=_auth(),
    )

    audit = client.get(
        f"/api/v1/incidents/{INCIDENT_ID}/recovery-audit",
        headers=_auth(),
    )

    assert audit.status_code == 200
    assert [item["event_type"] for item in audit.json()["items"]] == [
        "PLAN_CREATED",
        "PLAN_APPROVED",
        "EXECUTION_DISPATCHED",
    ]
    assert audit.json()["items"][1]["actor"] == "demo-operator"
    assert audit.json()["items"][2]["details"]["external_reference"] == (
        "github:workflow-dispatch:123456789"
    )


def test_verified_recovery_resolves_incident_and_rejects_conflicting_callback(
    recovery_runtime: tuple[TestClient, FakeRecoveryExecutor],
) -> None:
    """Catches dispatch being mistaken for success or conflicting callbacks rewriting history."""
    client, _ = recovery_runtime
    created = client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/recovery-plans",
        headers=_auth(),
    )
    plan_id = created.json()["id"]
    client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/recovery-plans/{plan_id}/approve",
        headers=_auth(),
        json={"actor": "demo-operator"},
    )
    dispatched = client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/recovery-plans/{plan_id}/execute",
        headers=_auth(),
    )
    attempt_id = dispatched.json()["id"]
    verification = {
        "idempotency_key": dispatched.json()["idempotency_key"],
        "status": "PASSED",
        "external_reference": "github:workflow-dispatch:123456789",
        "details": {"healthcheck": "passed", "rows_quarantined": 2},
    }

    first = client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/recovery-attempts/{attempt_id}/verification",
        headers=_auth(),
        json=verification,
    )
    repeated = client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/recovery-attempts/{attempt_id}/verification",
        headers=_auth(),
        json=verification,
    )
    conflicting = client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/recovery-attempts/{attempt_id}/verification",
        headers=_auth(),
        json={**verification, "status": "FAILED"},
    )
    incident = client.get(f"/api/v1/incidents/{INCIDENT_ID}")
    audit = client.get(
        f"/api/v1/incidents/{INCIDENT_ID}/recovery-audit",
        headers=_auth(),
    )

    assert first.status_code == 200
    assert first.json()["status"] == "VERIFIED"
    assert first.json()["verification_status"] == "PASSED"
    assert first.json()["verification_details"]["rows_quarantined"] == 2
    assert first.json()["duplicate"] is False
    assert repeated.status_code == 200
    assert repeated.json()["duplicate"] is True
    assert conflicting.status_code == 409
    assert conflicting.json() == {"detail": "Conflicting recovery verification"}
    assert incident.json()["status"] == "RESOLVED"
    assert audit.json()["items"][-1]["event_type"] == "VERIFICATION_PASSED"


def test_policy_denies_rollback_to_mutable_or_incomplete_image_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches a human approval turning an unpinned rollback target into production drift."""
    monkeypatch.setenv("DATAOPS_AGENT_TOKEN", "recovery-test-token")
    engine = _engine()
    _seed_validated_rca(
        engine,
        recommended_action={
            "type": "ROLLBACK_IMAGE",
            "rationale": "Restore the previous release.",
            "parameters": {
                "target_web_image": "ghcr.io/andyanh174/dataops-demo-web:latest",
                "target_api_image": "ghcr.io/andyanh174/dataops-demo-api:latest",
            },
            "requires_human_approval": True,
        },
    )
    with TestClient(
        create_app(
            engine=engine,
            log_store=EmptyLogStore(),
            evidence_sources=[],
        )
    ) as client:
        created = client.post(
            f"/api/v1/incidents/{INCIDENT_ID}/recovery-plans",
            headers=_auth(),
        )

    assert created.status_code == 201
    assert created.json()["policy_decision"] == "DENIED"
    assert created.json()["approval_status"] == "NOT_REQUIRED"
    assert created.json()["decision_reasons"] == [
        "Rollback requires immutable web/api image tags and a full commit revision"
    ]


def test_provider_failure_is_audited_and_reported_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches a provider outage being reported as an operator policy mistake."""
    monkeypatch.setenv("DATAOPS_AGENT_TOKEN", "recovery-test-token")
    engine = _engine()
    _seed_validated_rca(engine)
    executor = FailingRecoveryExecutor()
    with TestClient(
        create_app(
            engine=engine,
            log_store=EmptyLogStore(),
            evidence_sources=[],
            recovery_executor=executor,
        )
    ) as client:
        created = client.post(
            f"/api/v1/incidents/{INCIDENT_ID}/recovery-plans",
            headers=_auth(),
        )
        plan_id = created.json()["id"]
        client.post(
            f"/api/v1/incidents/{INCIDENT_ID}/recovery-plans/{plan_id}/approve",
            headers=_auth(),
            json={"actor": "demo-operator"},
        )
        executed = client.post(
            f"/api/v1/incidents/{INCIDENT_ID}/recovery-plans/{plan_id}/execute",
            headers=_auth(),
        )
        audit = client.get(
            f"/api/v1/incidents/{INCIDENT_ID}/recovery-audit",
            headers=_auth(),
        )

    assert executed.status_code == 503
    assert executed.json() == {"detail": "GitHub recovery dispatch failed (HTTP 503)"}
    assert len(executor.calls) == 1
    assert audit.json()["items"][-1]["event_type"] == "EXECUTION_FAILED"
