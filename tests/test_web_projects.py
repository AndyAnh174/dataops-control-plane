from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from dataops_control_plane.domain.models import (
    AppUser,
    Incident,
    PipelineRun,
    ProcessedEvent,
    Project,
    Workspace,
    WorkspaceMember,
)
from dataops_control_plane.main import create_app
from dataops_control_plane.services.pipeline_logs import LogWriteResult, PipelineLogDocument


class InMemoryLogStore:
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


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with TestClient(
        create_app(engine=engine, log_store=InMemoryLogStore()),
        base_url="https://testserver",
    ) as test_client:
        yield test_client


def bootstrap_owner(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/api/v1/auth/bootstrap",
        json={
            "email": "owner@example.com",
            "password": "correct horse battery staple",
            "workspace_name": "AndyAnh Lab",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_owner_can_create_and_list_a_project_in_their_workspace(
    client: TestClient,
) -> None:
    """Catches project onboarding that is not persisted or scoped to its workspace."""
    auth = bootstrap_owner(client)
    workspace_id = auth["workspaces"][0]["id"]

    created = client.post(
        f"/api/v1/workspaces/{workspace_id}/projects",
        json={
            "name": "Demo Pipeline",
            "provider": "github",
            "project_ref": "AndyAnh174/dataops-demo",
            "default_branch": "main",
        },
    )
    listed = client.get(f"/api/v1/workspaces/{workspace_id}/projects")

    assert created.status_code == 201
    assert created.json() == {
        "id": created.json()["id"],
        "workspace_id": workspace_id,
        "name": "Demo Pipeline",
        "provider": "github",
        "project_ref": "AndyAnh174/dataops-demo",
        "default_branch": "main",
    }
    assert listed.status_code == 200
    assert listed.json() == {"items": [created.json()]}


def test_browser_mutations_reject_a_cross_origin_request(client: TestClient) -> None:
    """Catches a malicious site using the owner's session cookie to mutate a workspace."""
    auth = bootstrap_owner(client)
    workspace_id = auth["workspaces"][0]["id"]

    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/projects",
        headers={"Origin": "https://attacker.example"},
        json={
            "name": "Forged project",
            "provider": "github",
            "project_ref": "attacker/forged",
            "default_branch": "main",
        },
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Cross-origin mutation is not allowed"}


def test_project_token_secret_is_returned_once_but_not_listed(
    client: TestClient,
) -> None:
    """Catches integration-token APIs leaking reusable secrets after creation."""
    auth = bootstrap_owner(client)
    workspace_id = auth["workspaces"][0]["id"]
    project = client.post(
        f"/api/v1/workspaces/{workspace_id}/projects",
        json={
            "name": "Demo Pipeline",
            "provider": "github",
            "project_ref": "AndyAnh174/dataops-demo",
            "default_branch": "main",
        },
    ).json()

    created = client.post(
        f"/api/v1/projects/{project['id']}/tokens",
        json={
            "name": "github-main",
            "scopes": ["runs:write", "logs:write", "reports:write"],
            "expires_in_days": 30,
        },
    )
    listed = client.get(f"/api/v1/projects/{project['id']}/tokens")

    assert created.status_code == 201
    secret = created.json()["token"]
    assert secret.startswith("dop_")
    assert created.json()["token_prefix"] == secret[:12]
    assert listed.status_code == 200
    assert listed.json()["items"] == [
        {
            "id": created.json()["id"],
            "project_id": project["id"],
            "name": "github-main",
            "token_prefix": secret[:12],
            "scopes": ["runs:write", "logs:write", "reports:write"],
            "expires_at": created.json()["expires_at"],
            "last_used_at": None,
            "revoked_at": None,
            "created_at": created.json()["created_at"],
        }
    ]
    assert secret not in listed.text


def test_project_token_can_ingest_only_its_own_pipeline_events(
    client: TestClient,
) -> None:
    """Catches one project token forging pipeline runs for another repository."""
    auth = bootstrap_owner(client)
    workspace_id = auth["workspaces"][0]["id"]
    project = client.post(
        f"/api/v1/workspaces/{workspace_id}/projects",
        json={
            "name": "Demo Pipeline",
            "provider": "github",
            "project_ref": "AndyAnh174/dataops-demo",
            "default_branch": "main",
        },
    ).json()
    token = client.post(
        f"/api/v1/projects/{project['id']}/tokens",
        json={
            "name": "github-main",
            "scopes": ["runs:write"],
            "expires_in_days": 30,
        },
    ).json()["token"]
    base_event = {
        "event_id": "github:project-token-owned",
        "event_type": "pipeline.started",
        "occurred_at": "2026-09-05T08:00:00Z",
        "provider": "github",
        "project_ref": "AndyAnh174/dataops-demo",
        "external_run_id": "token-run-1",
        "attempt": 1,
        "commit_sha": "abc1234",
        "branch": "main",
        "status": "RUNNING",
    }

    missing = client.post("/api/v1/events/pipeline", json=base_event)
    accepted = client.post(
        "/api/v1/events/pipeline",
        json=base_event,
        headers={"Authorization": f"Bearer {token}"},
    )
    cross_project = client.post(
        "/api/v1/events/pipeline",
        json={
            **base_event,
            "event_id": "github:project-token-forged",
            "project_ref": "someone-else/private-pipeline",
            "external_run_id": "token-run-2",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert missing.status_code == 401
    assert accepted.status_code == 202
    assert cross_project.status_code == 403
    assert cross_project.json() == {"detail": "Token does not belong to this pipeline project"}


def test_revoked_project_token_stops_authorizing_agent_requests(
    client: TestClient,
) -> None:
    """Catches token revocation that changes the UI state but leaves the secret usable."""
    auth = bootstrap_owner(client)
    workspace_id = auth["workspaces"][0]["id"]
    project = client.post(
        f"/api/v1/workspaces/{workspace_id}/projects",
        json={
            "name": "Demo Pipeline",
            "provider": "github",
            "project_ref": "AndyAnh174/dataops-demo",
            "default_branch": "main",
        },
    ).json()
    created = client.post(
        f"/api/v1/projects/{project['id']}/tokens",
        json={
            "name": "github-main",
            "scopes": ["runs:write"],
            "expires_in_days": 30,
        },
    ).json()

    revoked = client.delete(f"/api/v1/projects/{project['id']}/tokens/{created['id']}")
    replay = client.post(
        "/api/v1/events/pipeline",
        json={
            "event_id": "github:revoked-project-token",
            "event_type": "pipeline.started",
            "occurred_at": "2026-09-05T08:00:00Z",
            "provider": "github",
            "project_ref": "AndyAnh174/dataops-demo",
            "external_run_id": "token-run-revoked",
            "attempt": 1,
            "commit_sha": "abc1234",
            "branch": "main",
            "status": "RUNNING",
        },
        headers={"Authorization": f"Bearer {created['token']}"},
    )
    listed = client.get(f"/api/v1/projects/{project['id']}/tokens")

    assert revoked.status_code == 204
    assert replay.status_code == 401
    assert listed.json()["items"][0]["revoked_at"] is not None


def test_project_token_cannot_upload_a_report_to_another_projects_run(
    client: TestClient,
) -> None:
    """Catches a valid token attaching trusted quality evidence to another project."""
    auth = bootstrap_owner(client)
    workspace_id = auth["workspaces"][0]["id"]

    def create_project(name: str, project_ref: str) -> dict[str, object]:
        return client.post(
            f"/api/v1/workspaces/{workspace_id}/projects",
            json={
                "name": name,
                "provider": "github",
                "project_ref": project_ref,
                "default_branch": "main",
            },
        ).json()

    def create_token(project_id: object, name: str) -> str:
        return client.post(
            f"/api/v1/projects/{project_id}/tokens",
            json={
                "name": name,
                "scopes": ["runs:write", "reports:write"],
                "expires_in_days": 30,
            },
        ).json()["token"]

    first_project = create_project("First", "AndyAnh174/first-pipeline")
    second_project = create_project("Second", "AndyAnh174/second-pipeline")
    first_token = create_token(first_project["id"], "first-agent")
    second_token = create_token(second_project["id"], "second-agent")
    second_run = client.post(
        "/api/v1/events/pipeline",
        headers={"Authorization": f"Bearer {second_token}"},
        json={
            "event_id": "github:second-project-run",
            "event_type": "pipeline.started",
            "occurred_at": "2026-09-05T08:00:00Z",
            "provider": "github",
            "project_ref": "AndyAnh174/second-pipeline",
            "external_run_id": "second-run",
            "attempt": 1,
            "commit_sha": "abc1234",
            "branch": "main",
            "status": "RUNNING",
        },
    ).json()

    response = client.post(
        f"/api/v1/runs/{second_run['run_id']}/reports/data-quality",
        headers={"Authorization": f"Bearer {first_token}"},
        json={
            "schema_version": "1.0",
            "contract": {"name": "orders", "version": "1"},
            "scenario": "baseline",
            "success": True,
            "summary": {"checks": 1, "passed": 1, "failed": 0},
            "checks": [
                {
                    "id": "orders.not_empty",
                    "dimension": "volume",
                    "success": True,
                    "expectation": "orders contains rows",
                    "expected": {"min": 1},
                    "observed": {"rows": 5},
                }
            ],
            "dataset": {"row_count": 5, "columns": ["order_id"]},
            "generated_at": "2026-09-05T08:01:00Z",
        },
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Token does not belong to this pipeline run"}


def test_project_token_cannot_append_logs_to_another_projects_run(
    client: TestClient,
) -> None:
    """Catches a valid token contaminating another project's searchable pipeline logs."""
    auth = bootstrap_owner(client)
    workspace_id = auth["workspaces"][0]["id"]

    def setup_project(name: str, project_ref: str) -> tuple[dict[str, object], str]:
        project = client.post(
            f"/api/v1/workspaces/{workspace_id}/projects",
            json={
                "name": name,
                "provider": "github",
                "project_ref": project_ref,
                "default_branch": "main",
            },
        ).json()
        token = client.post(
            f"/api/v1/projects/{project['id']}/tokens",
            json={
                "name": f"{name.lower()}-agent",
                "scopes": ["runs:write", "logs:write"],
                "expires_in_days": 30,
            },
        ).json()["token"]
        return project, token

    _, first_token = setup_project("First", "AndyAnh174/first-pipeline")
    _, second_token = setup_project("Second", "AndyAnh174/second-pipeline")
    second_run = client.post(
        "/api/v1/events/pipeline",
        headers={"Authorization": f"Bearer {second_token}"},
        json={
            "event_id": "github:second-log-run",
            "event_type": "pipeline.started",
            "occurred_at": "2026-09-05T08:00:00Z",
            "provider": "github",
            "project_ref": "AndyAnh174/second-pipeline",
            "external_run_id": "second-log-run",
            "attempt": 1,
            "commit_sha": "abc1234",
            "branch": "main",
            "status": "RUNNING",
        },
    ).json()

    response = client.post(
        f"/api/v1/runs/{second_run['run_id']}/logs",
        headers={"Authorization": f"Bearer {first_token}"},
        json={
            "entries": [
                {
                    "occurred_at": "2026-09-05T08:01:00Z",
                    "job_name": "build",
                    "stage": "test",
                    "level": "ERROR",
                    "stream": "stderr",
                    "sequence": 1,
                    "message": "forged evidence",
                }
            ]
        },
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Token does not belong to this pipeline run"}


def test_project_token_cannot_invoke_operator_recovery_commands(
    client: TestClient,
) -> None:
    """Catches an ingestion credential crossing the human recovery approval boundary."""
    auth = bootstrap_owner(client)
    workspace_id = auth["workspaces"][0]["id"]
    project = client.post(
        f"/api/v1/workspaces/{workspace_id}/projects",
        json={
            "name": "Demo Pipeline",
            "provider": "github",
            "project_ref": "AndyAnh174/dataops-demo",
            "default_branch": "main",
        },
    ).json()
    token = client.post(
        f"/api/v1/projects/{project['id']}/tokens",
        json={
            "name": "github-main",
            "scopes": ["runs:write", "verification:write"],
            "expires_in_days": 30,
        },
    ).json()["token"]

    response = client.post(
        "/api/v1/incidents/00000000-0000-0000-0000-000000000001/recovery-plans",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Project tokens cannot perform operator actions"}


def test_authenticated_owner_session_reaches_operator_recovery_api(
    client: TestClient,
) -> None:
    """Catches the Web UI session being unable to reach operator-only recovery routes."""
    auth = bootstrap_owner(client)
    workspace_id = auth["workspaces"][0]["id"]
    project = client.post(
        f"/api/v1/workspaces/{workspace_id}/projects",
        json={
            "name": "Demo Pipeline",
            "provider": "github",
            "project_ref": "AndyAnh174/dataops-demo",
            "default_branch": "main",
        },
    ).json()
    client.post(
        f"/api/v1/projects/{project['id']}/tokens",
        json={
            "name": "github-main",
            "scopes": ["runs:write"],
            "expires_in_days": 30,
        },
    )

    response = client.post("/api/v1/incidents/00000000-0000-0000-0000-000000000001/recovery-plans")

    assert response.status_code == 404
    assert response.json() == {"detail": "Incident not found"}


def test_project_token_cannot_invoke_rca_operator_commands(client: TestClient) -> None:
    """Catches an ingestion credential triggering evidence or LLM operations."""
    auth = bootstrap_owner(client)
    workspace_id = auth["workspaces"][0]["id"]
    project = client.post(
        f"/api/v1/workspaces/{workspace_id}/projects",
        json={
            "name": "Demo Pipeline",
            "provider": "github",
            "project_ref": "AndyAnh174/dataops-demo",
            "default_branch": "main",
        },
    ).json()
    token = client.post(
        f"/api/v1/projects/{project['id']}/tokens",
        json={
            "name": "github-main",
            "scopes": ["runs:write"],
            "expires_in_days": 30,
        },
    ).json()["token"]

    response = client.get(
        "/api/v1/incidents/00000000-0000-0000-0000-000000000001/evidence",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Project tokens cannot perform operator actions"}


def test_bootstrapped_platform_does_not_expose_runs_or_incidents_without_login(
    client: TestClient,
) -> None:
    """Catches legacy read endpoints remaining public after Web authentication is enabled."""
    auth = bootstrap_owner(client)
    workspace_id = auth["workspaces"][0]["id"]
    project = client.post(
        f"/api/v1/workspaces/{workspace_id}/projects",
        json={
            "name": "Demo Pipeline",
            "provider": "github",
            "project_ref": "AndyAnh174/dataops-demo",
            "default_branch": "main",
        },
    ).json()
    token = client.post(
        f"/api/v1/projects/{project['id']}/tokens",
        json={
            "name": "github-main",
            "scopes": ["runs:write"],
            "expires_in_days": 30,
        },
    ).json()["token"]
    run_id = client.post(
        "/api/v1/events/pipeline",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "event_id": "github:private-read-run",
            "event_type": "pipeline.completed",
            "occurred_at": "2026-09-05T08:00:00Z",
            "provider": "github",
            "project_ref": "AndyAnh174/dataops-demo",
            "external_run_id": "private-read-run",
            "attempt": 1,
            "commit_sha": "abc1234",
            "branch": "main",
            "status": "FAILED",
            "failed_stage": "test",
        },
    ).json()["run_id"]
    client.cookies.clear()

    run = client.get(f"/api/v1/runs/{run_id}")
    incidents = client.get("/api/v1/incidents")

    assert run.status_code == 401
    assert incidents.status_code == 401


def test_owner_session_cannot_read_evidence_from_another_workspace(
    client: TestClient,
) -> None:
    """Catches a logged-in operator bypassing tenant isolation with a known incident ID."""
    bootstrap_owner(client)
    now = datetime.now(UTC)
    with Session(client.app.state.engine) as session:
        other_user = AppUser(
            email="other@example.com",
            password_hash="not-used-in-this-test",
            created_at=now,
        )
        session.add(other_user)
        session.flush()
        other_workspace = Workspace(
            name="Other workspace",
            created_by=other_user.id,
            created_at=now,
        )
        session.add(other_workspace)
        session.flush()
        session.add(
            WorkspaceMember(
                workspace_id=other_workspace.id,
                user_id=other_user.id,
                role="OWNER",
                created_at=now,
            )
        )
        session.add(
            Project(
                workspace_id=other_workspace.id,
                name="Private pipeline",
                provider="github",
                project_ref="other/private-pipeline",
                default_branch="main",
                created_at=now,
            )
        )
        pipeline_run = PipelineRun(
            provider="github",
            project_ref="other/private-pipeline",
            external_run_id="private-incident-run",
            attempt=1,
            commit_sha="abc1234",
            branch="main",
            status="FAILED",
            failed_stage="test",
            last_event_at=now,
        )
        session.add(pipeline_run)
        session.flush()
        event = ProcessedEvent(
            event_id="github:private-workspace-incident",
            pipeline_run_id=pipeline_run.id,
            received_at=now,
        )
        session.add(event)
        session.flush()
        incident = Incident(
            pipeline_run_id=pipeline_run.id,
            trigger_event_id=event.event_id,
            created_at=now,
            updated_at=now,
        )
        session.add(incident)
        session.commit()
        incident_id = incident.id

    response = client.get(f"/api/v1/incidents/{incident_id}/evidence")

    assert response.status_code == 404
    assert response.json() == {"detail": "Incident not found"}
