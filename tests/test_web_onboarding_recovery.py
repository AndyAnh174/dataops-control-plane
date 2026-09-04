from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from dataops_control_plane.domain.models import Incident, PipelineRun, ProcessedEvent, RCAReport
from dataops_control_plane.main import create_app
from dataops_control_plane.services.pipeline_logs import LogWriteResult, PipelineLogDocument

INCIDENT_ID = UUID("71000000-0000-0000-0000-000000000001")


class EmptyLogStore:
    def append(self, documents: Sequence[PipelineLogDocument]) -> LogWriteResult:
        return LogWriteResult(accepted_count=len(documents), duplicate_count=0)

    def search(
        self,
        run_id: UUID,
        *,
        query: str | None,
        stage: str | None,
        level: str | None,
        limit: int,
    ) -> list[PipelineLogDocument]:
        return []

    def close(self) -> None:
        pass


class FakeDispatch:
    external_reference = "github:workflow-dispatch:web-test"
    details = {"workflow": "dataops-recovery.yml"}


class FakeRecoveryExecutor:
    provider = "github"
    capabilities = frozenset({"RETRY", "QUARANTINE", "ROLLBACK_IMAGE"})

    def execute(self, request) -> FakeDispatch:
        return FakeDispatch()

    def close(self) -> None:
        pass


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATAOPS_PUBLIC_URL", "https://dataops.example.test")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with TestClient(
        create_app(
            engine=engine,
            log_store=EmptyLogStore(),
            evidence_sources=[],
            recovery_executor=FakeRecoveryExecutor(),
        ),
        base_url="https://testserver",
    ) as test_client:
        yield test_client


def _bootstrap_project(client: TestClient) -> dict[str, str]:
    auth = client.post(
        "/api/v1/auth/bootstrap",
        json={
            "email": "owner@example.com",
            "password": "correct horse battery staple",
            "workspace_name": "AndyAnh Lab",
        },
    ).json()
    project = client.post(
        f"/api/v1/workspaces/{auth['workspaces'][0]['id']}/projects",
        json={
            "name": "Demo Pipeline",
            "provider": "github",
            "project_ref": "AndyAnh174/dataops-demo",
            "default_branch": "main",
        },
    ).json()
    return project


def _seed_validated_incident(client: TestClient) -> None:
    timestamp = datetime(2026, 9, 5, 8, 30, tzinfo=UTC)
    run = PipelineRun(
        provider="github",
        project_ref="AndyAnh174/dataops-demo",
        external_run_id="web-recovery-42",
        attempt=1,
        commit_sha="e1437099e3aa3b0cd7b9c9db337081a55599fbb8",
        branch="main",
        status="FAILED",
        failed_stage="data-quality",
        last_event_at=timestamp,
    )
    event = ProcessedEvent(
        event_id="github:web-recovery:failed",
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
        incident_id=incident.id,
        analysis_status="VALIDATED",
        incident_type="DATA_QUALITY_VALIDITY",
        root_cause="Rows outside the amount contract caused the pipeline to fail.",
        confidence=0.93,
        evidence_claims=[
            {
                "citation_id": "EVD-DATA-QUALITY-001",
                "claim": "Two rows exceeded the accepted amount range.",
            }
        ],
        knowledge_document_ids=["a" * 64],
        recommended_action={
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
    with Session(client.app.state.engine) as session:
        session.add(run)
        session.flush()
        session.add(event)
        session.flush()
        session.add(incident)
        session.flush()
        session.add(report)
        session.commit()


def _create_pending_plan(client: TestClient) -> str:
    _seed_validated_incident(client)
    response = client.post(f"/api/v1/incidents/{INCIDENT_ID}/recovery-plans")
    assert response.status_code == 201
    return response.json()["id"]


def test_github_onboarding_returns_complete_files_without_leaking_a_token(
    client: TestClient,
) -> None:
    """Catches onboarding that requires users to guess files or exposes a reusable secret."""
    project = _bootstrap_project(client)

    response = client.get(f"/api/v1/projects/{project['id']}/onboarding/github")

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "github"
    assert body["project_id"] == project["id"]
    assert body["project_ref"] == "AndyAnh174/dataops-demo"
    assert body["workflow_path"] == ".github/workflows/dataops.yml"
    assert "uses: AndyAnh174/dataops-agent@v0" in body["workflow_yaml"]
    assert "DATAOPS_URL: ${{ secrets.DATAOPS_URL }}" in body["workflow_yaml"]
    assert "DATAOPS_TOKEN: ${{ secrets.DATAOPS_TOKEN }}" in body["workflow_yaml"]
    assert body["dataops_config_path"] == "dataops.yaml"
    assert "name: test" in body["dataops_config_yaml"]
    assert body["required_secrets"] == [
        {
            "name": "DATAOPS_URL",
            "value": "https://dataops.example.test",
            "sensitive": False,
            "description": "Public URL of this DataOps Platform",
        },
        {
            "name": "DATAOPS_TOKEN",
            "value": None,
            "sensitive": True,
            "description": "Project token shown once when it is created",
        },
    ]
    assert "dop_" not in response.text


def test_project_page_shows_copy_ready_github_onboarding(client: TestClient) -> None:
    """Catches the API having onboarding data that users cannot reach from the Web UI."""
    project = _bootstrap_project(client)

    response = client.get(f"/app/projects/{project['id']}")

    assert response.status_code == 200
    assert ".github/workflows/dataops.yml" in response.text
    assert "DATAOPS_URL" in response.text
    assert "AndyAnh174/dataops-agent@v0" in response.text
    assert "dataops.yaml" in response.text


def test_incident_page_exposes_rca_policy_and_pending_approval(client: TestClient) -> None:
    """Catches recovery state existing only in JSON APIs with no operator workflow."""
    _bootstrap_project(client)
    _create_pending_plan(client)

    response = client.get(f"/app/incidents/{INCIDENT_ID}")

    assert response.status_code == 200
    assert "Rows outside the amount contract" in response.text
    assert "QUARANTINE" in response.text
    assert "PENDING" in response.text
    assert "Approve plan" in response.text
    assert "Reject plan" in response.text
    assert "PLAN_CREATED" in response.text


def test_web_approval_actor_is_derived_from_the_authenticated_session(
    client: TestClient,
) -> None:
    """Catches a browser user forging another person's identity in the recovery audit."""
    _bootstrap_project(client)
    plan_id = _create_pending_plan(client)

    response = client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/recovery-plans/{plan_id}/approve",
        json={"actor": "attacker@example.com"},
    )

    assert response.status_code == 200
    assert response.json()["approved_by"] == "owner@example.com"


def test_cross_origin_browser_cannot_approve_a_recovery_plan(client: TestClient) -> None:
    """Catches a malicious origin using the owner's cookie to authorize recovery."""
    _bootstrap_project(client)
    plan_id = _create_pending_plan(client)

    response = client.post(
        f"/api/v1/incidents/{INCIDENT_ID}/recovery-plans/{plan_id}/approve",
        headers={"Origin": "https://attacker.example"},
        json={"actor": "owner@example.com"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Cross-origin mutation is not allowed"}
