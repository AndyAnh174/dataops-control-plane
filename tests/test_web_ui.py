from collections.abc import Iterator, Sequence
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine

from dataops_control_plane.main import create_app
from dataops_control_plane.services.pipeline_logs import LogWriteResult, PipelineLogDocument


class InMemoryLogStore:
    def __init__(self) -> None:
        self.documents: list[PipelineLogDocument] = []

    def append(self, documents: Sequence[PipelineLogDocument]) -> LogWriteResult:
        self.documents.extend(documents)
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
        return [document for document in self.documents if document.run_id == run_id][-limit:]


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


def test_root_guides_a_new_instance_to_setup_and_an_owner_to_the_dashboard(
    client: TestClient,
) -> None:
    """Catches the packaged Web UI lacking a usable first-run entry point."""
    first_visit = client.get("/", follow_redirects=False)
    setup_page = client.get("/setup")

    assert first_visit.status_code == 303
    assert first_visit.headers["location"] == "/setup"
    assert setup_page.status_code == 200
    assert "Create your DataOps owner" in setup_page.text

    created = client.post(
        "/api/v1/auth/bootstrap",
        json={
            "email": "owner@example.com",
            "password": "correct horse battery staple",
            "workspace_name": "AndyAnh Lab",
        },
    )
    owner_visit = client.get("/", follow_redirects=False)
    dashboard = client.get("/app")

    assert created.status_code == 201
    assert owner_visit.status_code == 303
    assert owner_visit.headers["location"] == "/app"
    assert dashboard.status_code == 200
    assert "AndyAnh Lab" in dashboard.text
    assert "Pipeline control center" in dashboard.text


def test_project_page_shows_its_pipeline_run_and_incident(
    client: TestClient,
) -> None:
    """Catches the Web UI onboarding projects without exposing their operational result."""
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
    token = client.post(
        f"/api/v1/projects/{project['id']}/tokens",
        json={
            "name": "github-main",
            "scopes": ["runs:write"],
            "expires_in_days": 30,
        },
    ).json()["token"]
    run = client.post(
        "/api/v1/events/pipeline",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "event_id": "github:web-ui-failed-run",
            "event_type": "pipeline.completed",
            "occurred_at": "2026-09-05T08:00:00Z",
            "provider": "github",
            "project_ref": "AndyAnh174/dataops-demo",
            "external_run_id": "web-ui-run-42",
            "attempt": 1,
            "commit_sha": "abc1234",
            "branch": "main",
            "status": "FAILED",
            "failed_stage": "data-quality",
        },
    )

    page = client.get(f"/app/projects/{project['id']}")

    assert run.status_code == 202
    assert page.status_code == 200
    assert "Demo Pipeline" in page.text
    assert "web-ui-run-42" in page.text
    assert "FAILED" in page.text
    assert "OPEN" in page.text


def test_web_responses_set_browser_security_headers(client: TestClient) -> None:
    """Catches the public Web UI being frameable or allowed to load arbitrary scripts."""
    response = client.get("/setup")

    assert response.status_code == 200
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["content-security-policy"] == (
        "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
        "form-action 'self'; object-src 'none'"
    )


def test_run_page_shows_correlated_logs_without_exposing_elasticsearch(
    client: TestClient,
) -> None:
    """Catches the user dashboard requiring direct Kibana access to inspect a failed run."""
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
    token = client.post(
        f"/api/v1/projects/{project['id']}/tokens",
        json={
            "name": "github-main",
            "scopes": ["runs:write", "logs:write"],
            "expires_in_days": 30,
        },
    ).json()["token"]
    run = client.post(
        "/api/v1/events/pipeline",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "event_id": "github:web-log-run",
            "event_type": "pipeline.completed",
            "occurred_at": "2026-09-05T08:00:00Z",
            "provider": "github",
            "project_ref": "AndyAnh174/dataops-demo",
            "external_run_id": "web-log-run",
            "attempt": 1,
            "commit_sha": "abc1234",
            "branch": "main",
            "status": "FAILED",
            "failed_stage": "data-quality",
        },
    ).json()
    logged = client.post(
        f"/api/v1/runs/{run['run_id']}/logs",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "entries": [
                {
                    "occurred_at": "2026-09-05T08:00:01Z",
                    "job_name": "quality",
                    "stage": "data-quality",
                    "level": "ERROR",
                    "stream": "stderr",
                    "sequence": 1,
                    "message": "Schema validation failed for customer_id",
                }
            ]
        },
    )

    page = client.get(f"/app/runs/{run['run_id']}")

    assert logged.status_code == 202
    assert page.status_code == 200
    assert "web-log-run" in page.text
    assert "Schema validation failed for customer_id" in page.text
    assert "data-quality" in page.text
