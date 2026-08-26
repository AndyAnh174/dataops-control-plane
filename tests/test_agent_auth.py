from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine

from dataops_control_plane.main import create_app


@pytest.fixture
def protected_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATAOPS_AGENT_TOKEN", "local-agent-test-token")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with TestClient(create_app(engine=engine)) as test_client:
        yield test_client


def test_pipeline_ingestion_rejects_a_missing_or_wrong_agent_token(
    protected_client: TestClient,
) -> None:
    """Catches a public caller being able to forge pipeline telemetry."""
    payload = {
        "event_id": "github:protected-event",
        "event_type": "pipeline.started",
        "occurred_at": "2026-08-26T15:00:00Z",
        "provider": "github",
        "project_ref": "example/protected-pipeline",
        "external_run_id": "protected-501",
        "attempt": 1,
        "commit_sha": "a51e092",
        "branch": "main",
        "status": "RUNNING",
    }

    missing = protected_client.post("/api/v1/events/pipeline", json=payload)
    wrong = protected_client.post(
        "/api/v1/events/pipeline",
        json=payload,
        headers={"Authorization": "Bearer wrong-token"},
    )
    accepted = protected_client.post(
        "/api/v1/events/pipeline",
        json=payload,
        headers={"Authorization": "Bearer local-agent-test-token"},
    )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert accepted.status_code == 202


def test_pipeline_logs_are_not_readable_without_the_agent_token(
    protected_client: TestClient,
) -> None:
    """Catches redacted but operationally sensitive logs being publicly searchable."""
    response = protected_client.get("/api/v1/runs/00000000-0000-0000-0000-000000000001/logs")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
