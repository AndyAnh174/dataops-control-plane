from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from dataops_control_plane.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with TestClient(create_app(engine=engine)) as test_client:
        yield test_client


def pipeline_event(
    *,
    event_id: str,
    status: str,
    occurred_at: str,
    event_type: str = "pipeline.completed",
    failed_stage: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "event_id": event_id,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "provider": "github",
        "project_ref": "example/customer-pipeline",
        "external_run_id": "incident-run-501",
        "attempt": 2,
        "commit_sha": "abc1234",
        "branch": "main",
        "status": status,
    }
    if failed_stage is not None:
        payload["failed_stage"] = failed_stage
    return payload


def test_failed_pipeline_creates_an_open_incident_linked_to_the_run(
    client: TestClient,
) -> None:
    """Catches a failed pipeline being stored without an actionable Incident."""
    failed = client.post(
        "/api/v1/events/pipeline",
        json=pipeline_event(
            event_id="github:incident-failed",
            status="FAILED",
            occurred_at="2026-08-26T15:02:00Z",
            failed_stage="data-quality",
        ),
    )

    listed = client.get("/api/v1/incidents")

    assert failed.status_code == 202
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1
    incident = listed.json()["items"][0]
    assert incident["status"] == "OPEN"
    assert incident["trigger_event_id"] == "github:incident-failed"
    assert incident["pipeline_run"] == {
        "id": failed.json()["run_id"],
        "provider": "github",
        "project_ref": "example/customer-pipeline",
        "external_run_id": "incident-run-501",
        "attempt": 2,
        "commit_sha": "abc1234",
        "branch": "main",
        "status": "FAILED",
        "failed_stage": "data-quality",
        "last_event_at": "2026-08-26T15:02:00Z",
    }
    assert datetime.fromisoformat(incident["created_at"]).tzinfo is not None
    assert incident["updated_at"] == incident["created_at"]

    detail = client.get(f"/api/v1/incidents/{incident['id']}")

    assert detail.status_code == 200
    assert detail.json() == incident


def test_repeated_failed_events_for_one_run_do_not_duplicate_the_incident(
    client: TestClient,
) -> None:
    """Catches callback retries creating multiple Incidents for one run attempt."""
    started = client.post(
        "/api/v1/events/pipeline",
        json=pipeline_event(
            event_id="github:incident-started",
            event_type="pipeline.started",
            status="RUNNING",
            occurred_at="2026-08-26T15:00:00Z",
        ),
    )
    first_failure = client.post(
        "/api/v1/events/pipeline",
        json=pipeline_event(
            event_id="github:incident-first-failure",
            status="FAILED",
            occurred_at="2026-08-26T15:02:00Z",
            failed_stage="data-quality",
        ),
    )
    repeated_failure = client.post(
        "/api/v1/events/pipeline",
        json=pipeline_event(
            event_id="github:incident-second-failure",
            status="FAILED",
            occurred_at="2026-08-26T15:03:00Z",
            failed_stage="data-quality",
        ),
    )

    listed = client.get("/api/v1/incidents")

    assert started.status_code == 202
    assert first_failure.status_code == 202
    assert repeated_failure.status_code == 202
    assert first_failure.json()["run_id"] == started.json()["run_id"]
    assert repeated_failure.json()["run_id"] == started.json()["run_id"]
    assert len(listed.json()["items"]) == 1
    assert listed.json()["items"][0]["trigger_event_id"] == ("github:incident-first-failure")


@pytest.mark.parametrize("terminal_status", ["SUCCESS", "CANCELED"])
def test_non_failure_terminal_statuses_do_not_create_incidents(
    client: TestClient,
    terminal_status: str,
) -> None:
    """Catches successful or canceled runs being misclassified as failures."""
    completed = client.post(
        "/api/v1/events/pipeline",
        json=pipeline_event(
            event_id=f"github:incident-{terminal_status.lower()}",
            status=terminal_status,
            occurred_at="2026-08-26T15:02:00Z",
        ),
    )

    listed = client.get("/api/v1/incidents")

    assert completed.status_code == 202
    assert listed.status_code == 200
    assert listed.json() == {"items": []}


def test_unknown_incident_returns_not_found(client: TestClient) -> None:
    """Catches an unknown Incident being exposed as an internal server error."""
    response = client.get("/api/v1/incidents/00000000-0000-0000-0000-000000000001")

    assert response.status_code == 404
    assert response.json() == {"detail": "Incident not found"}
