from collections.abc import Iterator

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


def test_pipeline_started_event_creates_running_run(client: TestClient) -> None:
    """Catches ingestion that acknowledges an event without creating its run."""
    response = client.post(
        "/api/v1/events/pipeline",
        json={
            "event_id": "github:delivery-001",
            "event_type": "pipeline.started",
            "occurred_at": "2026-08-26T10:15:00Z",
            "provider": "github",
            "project_ref": "example/data-pipeline",
            "external_run_id": "875421",
            "attempt": 1,
            "commit_sha": "a51e092",
            "branch": "main",
            "status": "RUNNING",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["event_id"] == "github:delivery-001"
    assert body["duplicate"] is False
    assert body["run_status"] == "RUNNING"
    assert body["run_id"]


def test_repeated_event_is_idempotent(client: TestClient) -> None:
    """Catches duplicate webhook delivery creating more than one logical run."""
    payload = {
        "event_id": "github:delivery-duplicate",
        "event_type": "pipeline.started",
        "occurred_at": "2026-08-26T10:15:00Z",
        "provider": "github",
        "project_ref": "example/data-pipeline",
        "external_run_id": "875422",
        "attempt": 1,
        "commit_sha": "b62f103",
        "branch": "main",
        "status": "RUNNING",
    }

    first = client.post("/api/v1/events/pipeline", json=payload)
    repeated = client.post("/api/v1/events/pipeline", json=payload)

    assert first.status_code == 202
    assert repeated.status_code == 202
    assert repeated.json() == {
        "event_id": "github:delivery-duplicate",
        "run_id": first.json()["run_id"],
        "duplicate": True,
        "run_status": "RUNNING",
    }


def test_completion_event_updates_the_existing_run(client: TestClient) -> None:
    """Catches a completion webhook being stored as a second pipeline run."""
    started = client.post(
        "/api/v1/events/pipeline",
        json={
            "event_id": "github:delivery-started",
            "event_type": "pipeline.started",
            "occurred_at": "2026-08-26T10:15:00Z",
            "provider": "github",
            "project_ref": "example/data-pipeline",
            "external_run_id": "875423",
            "attempt": 1,
            "commit_sha": "c73a214",
            "branch": "main",
            "status": "RUNNING",
        },
    )
    completed = client.post(
        "/api/v1/events/pipeline",
        json={
            "event_id": "github:delivery-completed",
            "event_type": "pipeline.completed",
            "occurred_at": "2026-08-26T10:17:00Z",
            "provider": "github",
            "project_ref": "example/data-pipeline",
            "external_run_id": "875423",
            "attempt": 1,
            "commit_sha": "c73a214",
            "branch": "main",
            "status": "FAILED",
            "failed_stage": "data-quality",
        },
    )

    assert started.status_code == 202
    assert completed.status_code == 202
    assert completed.json() == {
        "event_id": "github:delivery-completed",
        "run_id": started.json()["run_id"],
        "duplicate": False,
        "run_status": "FAILED",
    }


def test_terminal_run_cannot_return_to_running(client: TestClient) -> None:
    """Catches stale events reopening a pipeline run that already succeeded."""
    base = {
        "event_type": "pipeline.completed",
        "provider": "github",
        "project_ref": "example/data-pipeline",
        "external_run_id": "875424",
        "attempt": 1,
        "commit_sha": "d84b325",
        "branch": "main",
    }
    completed = client.post(
        "/api/v1/events/pipeline",
        json={
            **base,
            "event_id": "github:delivery-success",
            "occurred_at": "2026-08-26T10:20:00Z",
            "status": "SUCCESS",
        },
    )
    stale_started = client.post(
        "/api/v1/events/pipeline",
        json={
            **base,
            "event_id": "github:delivery-stale-start",
            "event_type": "pipeline.started",
            "occurred_at": "2026-08-26T10:19:00Z",
            "status": "RUNNING",
        },
    )

    assert completed.status_code == 202
    assert stale_started.status_code == 409
    assert stale_started.json() == {
        "detail": "Invalid pipeline status transition: SUCCESS -> RUNNING"
    }


def test_created_run_can_be_read_back(client: TestClient) -> None:
    """Catches an ingestion path that stores a run but cannot expose its normalized state."""
    created = client.post(
        "/api/v1/events/pipeline",
        json={
            "event_id": "gitlab:delivery-readable",
            "event_type": "pipeline.started",
            "occurred_at": "2026-08-26T11:00:00Z",
            "provider": "gitlab",
            "project_ref": "example/portable-pipeline",
            "external_run_id": "99100",
            "attempt": 2,
            "commit_sha": "e95c436",
            "branch": "develop",
            "status": "RUNNING",
        },
    )

    response = client.get(f"/api/v1/runs/{created.json()['run_id']}")

    assert response.status_code == 200
    assert response.json() == {
        "id": created.json()["run_id"],
        "provider": "gitlab",
        "project_ref": "example/portable-pipeline",
        "external_run_id": "99100",
        "attempt": 2,
        "commit_sha": "e95c436",
        "branch": "develop",
        "status": "RUNNING",
        "failed_stage": None,
        "last_event_at": "2026-08-26T11:00:00Z",
    }


def test_unknown_run_returns_not_found(client: TestClient) -> None:
    """Catches a missing run being exposed as an internal server error."""
    with TestClient(client.app, raise_server_exceptions=False) as safe_client:
        response = safe_client.get("/api/v1/runs/00000000-0000-0000-0000-000000000001")

    assert response.status_code == 404
    assert response.json() == {"detail": "Pipeline run not found"}


def test_application_startup_initializes_database_schema() -> None:
    """Catches a fresh deployment starting without the tables required by ingestion."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with TestClient(create_app(engine=engine)) as fresh_client:
        response = fresh_client.post(
            "/api/v1/events/pipeline",
            json={
                "event_id": "jenkins:delivery-fresh-db",
                "event_type": "pipeline.started",
                "occurred_at": "2026-08-26T12:00:00Z",
                "provider": "jenkins",
                "project_ref": "example/jenkins-pipeline",
                "external_run_id": "build-501",
                "attempt": 1,
                "commit_sha": "f06d547",
                "branch": "main",
                "status": "RUNNING",
            },
        )

    assert response.status_code == 202
