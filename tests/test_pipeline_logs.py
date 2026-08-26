from collections.abc import Iterator, Sequence
from dataclasses import replace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from dataops_control_plane.main import create_app
from dataops_control_plane.services.pipeline_logs import (
    LogWriteResult,
    PipelineLogDocument,
    redact_log_text,
)


class InMemoryLogStore:
    def __init__(self) -> None:
        self._documents: dict[str, PipelineLogDocument] = {}

    def append(self, documents: Sequence[PipelineLogDocument]) -> LogWriteResult:
        accepted_count = 0
        duplicate_count = 0
        for document in documents:
            if document.event_hash in self._documents:
                duplicate_count += 1
                continue
            self._documents[document.event_hash] = replace(document)
            accepted_count += 1
        return LogWriteResult(
            accepted_count=accepted_count,
            duplicate_count=duplicate_count,
        )

    def search(
        self,
        run_id: UUID,
        *,
        query: str | None,
        stage: str | None,
        level: str | None,
        limit: int,
    ) -> list[PipelineLogDocument]:
        documents = [document for document in self._documents.values() if document.run_id == run_id]
        if query is not None:
            documents = [
                document
                for document in documents
                if query.casefold() in document.message.casefold()
            ]
        if stage is not None:
            documents = [document for document in documents if document.stage == stage]
        if level is not None:
            documents = [document for document in documents if document.level == level]
        return sorted(documents, key=lambda document: (document.occurred_at, document.sequence))[
            -limit:
        ]


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with TestClient(create_app(engine=engine, log_store=InMemoryLogStore())) as test_client:
        yield test_client


def create_failed_run(client: TestClient, external_run_id: str = "pipeline-log-run") -> str:
    response = client.post(
        "/api/v1/events/pipeline",
        json={
            "event_id": f"github:{external_run_id}",
            "event_type": "pipeline.completed",
            "occurred_at": "2026-08-26T14:10:00Z",
            "provider": "github",
            "project_ref": "example/data-pipeline",
            "external_run_id": external_run_id,
            "attempt": 1,
            "commit_sha": "a51e092",
            "branch": "main",
            "status": "FAILED",
            "failed_stage": "data-quality",
        },
    )
    assert response.status_code == 202
    return response.json()["run_id"]


def test_log_ingestion_redacts_secret_before_it_can_be_searched(client: TestClient) -> None:
    """Catches a CI credential being persisted in searchable log storage."""
    run_id = create_failed_run(client)

    ingested = client.post(
        f"/api/v1/runs/{run_id}/logs",
        json={
            "entries": [
                {
                    "occurred_at": "2026-08-26T14:09:58Z",
                    "job_name": "quality",
                    "stage": "data-quality",
                    "level": "error",
                    "stream": "stderr",
                    "sequence": 7,
                    "message": (
                        "Schema validation failed; Authorization: Bearer "
                        "ghp_abcdefghijklmnopqrstuvwxyz123456"
                    ),
                    "tags": ["ci", "quality"],
                    "metadata": {
                        "table": "customers",
                        "request": {"authorization": "Bearer metadata-secret-value"},
                    },
                }
            ]
        },
    )
    searched = client.get(
        f"/api/v1/runs/{run_id}/logs",
        params={"query": "Schema validation", "stage": "data-quality"},
    )

    assert ingested.status_code == 202
    assert ingested.json() == {
        "run_id": run_id,
        "accepted_count": 1,
        "duplicate_count": 0,
        "redaction_count": 2,
    }
    assert searched.status_code == 200
    assert searched.json() == {
        "items": [
            {
                "occurred_at": "2026-08-26T14:09:58Z",
                "job_name": "quality",
                "stage": "data-quality",
                "level": "ERROR",
                "stream": "stderr",
                "sequence": 7,
                "message": "Schema validation failed; Authorization: Bearer [REDACTED]",
                "tags": ["ci", "quality"],
                "metadata": {
                    "table": "customers",
                    "request": {"authorization": "Bearer [REDACTED]"},
                },
                "redaction_count": 2,
            }
        ]
    }
    assert "ghp_" not in searched.text
    assert "metadata-secret-value" not in searched.text


def test_repeated_log_batch_is_idempotent(client: TestClient) -> None:
    """Catches callback retries storing the same pipeline line more than once."""
    run_id = create_failed_run(client, external_run_id="pipeline-log-duplicate")
    payload = {
        "entries": [
            {
                "occurred_at": "2026-08-26T14:09:59Z",
                "job_name": "quality",
                "stage": "pytest",
                "level": "ERROR",
                "stream": "stdout",
                "sequence": 12,
                "message": "Expected 42 rows but received 41",
            }
        ]
    }

    first = client.post(f"/api/v1/runs/{run_id}/logs", json=payload)
    repeated = client.post(f"/api/v1/runs/{run_id}/logs", json=payload)
    searched = client.get(f"/api/v1/runs/{run_id}/logs")

    assert first.status_code == 202
    assert first.json()["accepted_count"] == 1
    assert first.json()["duplicate_count"] == 0
    assert repeated.status_code == 202
    assert repeated.json()["accepted_count"] == 0
    assert repeated.json()["duplicate_count"] == 1
    assert len(searched.json()["items"]) == 1


def test_logs_cannot_be_attached_to_an_unknown_run(client: TestClient) -> None:
    """Catches orphaned logs that cannot be correlated with a pipeline run."""
    response = client.post(
        "/api/v1/runs/00000000-0000-0000-0000-000000000001/logs",
        json={
            "entries": [
                {
                    "occurred_at": "2026-08-26T14:09:59Z",
                    "job_name": "quality",
                    "stage": "pytest",
                    "level": "ERROR",
                    "stream": "stderr",
                    "sequence": 1,
                    "message": "Failure",
                }
            ]
        },
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Pipeline run not found"}


def test_log_ingestion_normalizes_a_naive_timestamp_to_utc(client: TestClient) -> None:
    """Catches runner-local timestamps being indexed with an ambiguous timezone."""
    run_id = create_failed_run(client, external_run_id="pipeline-log-naive-time")

    response = client.post(
        f"/api/v1/runs/{run_id}/logs",
        json={
            "entries": [
                {
                    "occurred_at": "2026-08-26T14:09:59",
                    "job_name": "quality",
                    "stage": "pytest",
                    "level": "ERROR",
                    "stream": "stderr",
                    "sequence": 1,
                    "message": "Failure",
                }
            ]
        },
    )
    searched = client.get(f"/api/v1/runs/{run_id}/logs")

    assert response.status_code == 202
    assert searched.json()["items"][0]["occurred_at"] == "2026-08-26T14:09:59Z"


def test_redactor_masks_database_passwords_and_api_keys() -> None:
    """Catches structured credentials that do not use an Authorization header."""
    redacted, count = redact_log_text(
        "DATABASE_URL=postgresql://dataops:super-secret@postgres/dataops api_key=topsecret123"
    )

    assert redacted == (
        "DATABASE_URL=postgresql://dataops:[REDACTED]@postgres/dataops api_key=[REDACTED]"
    )
    assert count == 2
