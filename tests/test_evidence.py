from collections.abc import Iterator, Sequence
from dataclasses import replace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from dataops_control_plane.main import create_app
from dataops_control_plane.services.pipeline_logs import (
    LogStoreUnavailable,
    LogWriteResult,
    PipelineLogDocument,
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


class UnavailableLogStore(InMemoryLogStore):
    def search(
        self,
        run_id: UUID,
        *,
        query: str | None,
        stage: str | None,
        level: str | None,
        limit: int,
    ) -> list[PipelineLogDocument]:
        raise LogStoreUnavailable


class UnredactedLogStore(InMemoryLogStore):
    def search(
        self,
        run_id: UUID,
        *,
        query: str | None,
        stage: str | None,
        level: str | None,
        limit: int,
    ) -> list[PipelineLogDocument]:
        documents = super().search(
            run_id,
            query=query,
            stage=stage,
            level=level,
            limit=limit,
        )
        return [
            replace(
                document,
                message="quality failed api_key=collector-must-redact-this-secret",
                redaction_count=0,
            )
            for document in documents
        ]


def build_test_client(
    log_store: InMemoryLogStore,
    *,
    raise_server_exceptions: bool = True,
) -> TestClient:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return TestClient(
        create_app(engine=engine, log_store=log_store),
        raise_server_exceptions=raise_server_exceptions,
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    with build_test_client(InMemoryLogStore()) as test_client:
        yield test_client


def create_failed_incident(client: TestClient, external_run_id: str) -> tuple[str, str]:
    failed = client.post(
        "/api/v1/events/pipeline",
        json={
            "event_id": f"local:{external_run_id}:failed",
            "event_type": "pipeline.completed",
            "occurred_at": "2026-08-26T14:10:00Z",
            "provider": "local",
            "project_ref": "example/customer-pipeline",
            "external_run_id": external_run_id,
            "attempt": 1,
            "commit_sha": "a51e092",
            "branch": "main",
            "status": "FAILED",
            "failed_stage": "data-quality",
        },
    )
    listed = client.get("/api/v1/incidents")

    assert failed.status_code == 202
    assert listed.status_code == 200
    return listed.json()["items"][0]["id"], failed.json()["run_id"]


def ingest_pipeline_logs(client: TestClient, run_id: str) -> None:
    response = client.post(
        f"/api/v1/runs/{run_id}/logs",
        json={
            "entries": [
                {
                    "occurred_at": "2026-08-26T14:09:57Z",
                    "job_name": "build",
                    "stage": "container-build",
                    "level": "INFO",
                    "stream": "stdout",
                    "sequence": 6,
                    "message": "Container build completed",
                },
                {
                    "occurred_at": "2026-08-26T14:09:58Z",
                    "job_name": "quality",
                    "stage": "data-quality",
                    "level": "ERROR",
                    "stream": "stderr",
                    "sequence": 7,
                    "message": (
                        "Schema validation failed; Authorization: Bearer "
                        "github_pat_abcdefghijklmnopqrstuvwxyz123456"
                    ),
                    "metadata": {"table": "customers"},
                },
            ]
        },
    )
    assert response.status_code == 202


def test_collect_evidence_builds_a_citable_bundle_from_run_metadata_and_failed_stage_logs(
    client: TestClient,
) -> None:
    """Catches a collector that stores an Incident without its run facts or failure log."""
    incident_id, run_id = create_failed_incident(client, "evidence-run-501")
    ingest_pipeline_logs(client, run_id)

    collected = client.post(f"/api/v1/incidents/{incident_id}/collect-evidence")
    listed = client.get(f"/api/v1/incidents/{incident_id}/evidence")
    incident = client.get(f"/api/v1/incidents/{incident_id}")

    assert collected.status_code == 200
    assert collected.json() == {
        "incident_id": incident_id,
        "incident_status": "ANALYZING",
        "collected_count": 2,
        "duplicate_count": 0,
        "evidence_count": 2,
        "warnings": [],
    }
    assert listed.status_code == 200
    assert incident.json()["status"] == "ANALYZING"

    items_by_type = {item["evidence_type"]: item for item in listed.json()["items"]}
    assert set(items_by_type) == {"PIPELINE_METADATA", "LOG_EXCERPT"}

    metadata = items_by_type["PIPELINE_METADATA"]
    assert metadata["source_uri"] == f"postgresql://pipeline-runs/{run_id}"
    assert metadata["metadata"] == {
        "run_id": run_id,
        "provider": "local",
        "project_ref": "example/customer-pipeline",
        "external_run_id": "evidence-run-501",
        "attempt": 1,
        "commit_sha": "a51e092",
        "branch": "main",
        "status": "FAILED",
        "failed_stage": "data-quality",
        "last_event_at": "2026-08-26T14:10:00Z",
    }

    log_excerpt = items_by_type["LOG_EXCERPT"]
    assert log_excerpt["source_uri"] == (
        f"elasticsearch://pipeline-logs/runs/{run_id}?stage=data-quality&limit=100"
    )
    assert "Schema validation failed" in log_excerpt["excerpt"]
    assert "Authorization: Bearer [REDACTED]" in log_excerpt["excerpt"]
    assert "Container build completed" not in log_excerpt["excerpt"]
    assert "github_pat_" not in listed.text
    assert log_excerpt["metadata"] == {
        "run_id": run_id,
        "stage": "data-quality",
        "log_count": 1,
        "levels": ["ERROR"],
        "source_redaction_count": 1,
        "collector_redaction_count": 0,
        "truncated": False,
    }

    for item in listed.json()["items"]:
        assert item["incident_id"] == incident_id
        assert item["citation_id"].startswith("EVD-")
        assert len(item["checksum"]) == 64
        assert item["collected_at"].endswith("Z")


def test_collect_evidence_is_idempotent_for_an_unchanged_source_bundle(
    client: TestClient,
) -> None:
    """Catches collector retries inserting duplicate citations for identical evidence."""
    incident_id, run_id = create_failed_incident(client, "evidence-run-retry")
    ingest_pipeline_logs(client, run_id)

    first = client.post(f"/api/v1/incidents/{incident_id}/collect-evidence")
    first_items = client.get(f"/api/v1/incidents/{incident_id}/evidence").json()["items"]
    repeated = client.post(f"/api/v1/incidents/{incident_id}/collect-evidence")
    repeated_items = client.get(f"/api/v1/incidents/{incident_id}/evidence").json()["items"]

    assert first.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json() == {
        "incident_id": incident_id,
        "incident_status": "ANALYZING",
        "collected_count": 0,
        "duplicate_count": 2,
        "evidence_count": 2,
        "warnings": [],
    }
    assert repeated_items == first_items


def test_collect_evidence_requires_failed_stage_logs_before_analysis(
    client: TestClient,
) -> None:
    """Catches an Incident entering analysis with no failure log evidence."""
    incident_id, _ = create_failed_incident(client, "evidence-run-no-logs")

    collected = client.post(f"/api/v1/incidents/{incident_id}/collect-evidence")
    listed = client.get(f"/api/v1/incidents/{incident_id}/evidence")

    assert collected.status_code == 200
    assert collected.json() == {
        "incident_id": incident_id,
        "incident_status": "ACTION_REQUIRED",
        "collected_count": 1,
        "duplicate_count": 0,
        "evidence_count": 1,
        "warnings": [
            {
                "source": "pipeline_logs",
                "code": "NO_MATCHING_LOGS",
                "message": "No logs found for failed stage 'data-quality'",
            }
        ],
    }
    assert [item["evidence_type"] for item in listed.json()["items"]] == ["PIPELINE_METADATA"]


def test_unknown_incident_cannot_collect_or_expose_evidence(client: TestClient) -> None:
    """Catches evidence being created without an owning Incident."""
    unknown_id = "00000000-0000-0000-0000-000000000001"

    collected = client.post(f"/api/v1/incidents/{unknown_id}/collect-evidence")
    listed = client.get(f"/api/v1/incidents/{unknown_id}/evidence")

    assert collected.status_code == 404
    assert collected.json() == {"detail": "Incident not found"}
    assert listed.status_code == 404
    assert listed.json() == {"detail": "Incident not found"}


def test_unavailable_log_storage_preserves_metadata_and_requires_action() -> None:
    """Catches an Elasticsearch outage losing all evidence or crashing collection."""
    with build_test_client(
        UnavailableLogStore(),
        raise_server_exceptions=False,
    ) as client:
        incident_id, _ = create_failed_incident(client, "evidence-run-es-unavailable")

        collected = client.post(f"/api/v1/incidents/{incident_id}/collect-evidence")
        listed = client.get(f"/api/v1/incidents/{incident_id}/evidence")

    assert collected.status_code == 200
    assert collected.json() == {
        "incident_id": incident_id,
        "incident_status": "ACTION_REQUIRED",
        "collected_count": 1,
        "duplicate_count": 0,
        "evidence_count": 1,
        "warnings": [
            {
                "source": "pipeline_logs",
                "code": "SOURCE_UNAVAILABLE",
                "message": "Pipeline log storage is temporarily unavailable",
            }
        ],
    }
    assert [item["evidence_type"] for item in listed.json()["items"]] == ["PIPELINE_METADATA"]


def test_log_evidence_is_truncated_to_a_bounded_context(client: TestClient) -> None:
    """Catches an unbounded log excerpt exhausting later model context or database storage."""
    incident_id, run_id = create_failed_incident(client, "evidence-run-long-log")
    ingested = client.post(
        f"/api/v1/runs/{run_id}/logs",
        json={
            "entries": [
                {
                    "occurred_at": "2026-08-26T14:09:58Z",
                    "job_name": "quality",
                    "stage": "data-quality",
                    "level": "ERROR",
                    "stream": "stderr",
                    "sequence": 7,
                    "message": "x" * 25_000,
                }
            ]
        },
    )

    collected = client.post(f"/api/v1/incidents/{incident_id}/collect-evidence")
    evidence = client.get(f"/api/v1/incidents/{incident_id}/evidence").json()["items"]
    log_excerpt = next(item for item in evidence if item["evidence_type"] == "LOG_EXCERPT")

    assert ingested.status_code == 202
    assert collected.status_code == 200
    assert len(log_excerpt["excerpt"]) <= 20_000
    assert log_excerpt["excerpt"].endswith("...[TRUNCATED]")
    assert log_excerpt["metadata"]["truncated"] is True


def test_collector_redacts_a_secret_even_if_the_log_source_returns_it() -> None:
    """Catches evidence trusting an upstream store that contains unredacted credentials."""
    with build_test_client(UnredactedLogStore()) as client:
        incident_id, run_id = create_failed_incident(client, "evidence-run-defense-redaction")
        ingest_pipeline_logs(client, run_id)

        collected = client.post(f"/api/v1/incidents/{incident_id}/collect-evidence")
        listed = client.get(f"/api/v1/incidents/{incident_id}/evidence")

    log_excerpt = next(
        item for item in listed.json()["items"] if item["evidence_type"] == "LOG_EXCERPT"
    )
    assert collected.status_code == 200
    assert "collector-must-redact-this-secret" not in listed.text
    assert "api_key=[REDACTED]" in log_excerpt["excerpt"]
    assert log_excerpt["metadata"]["collector_redaction_count"] == 1


def test_collect_evidence_includes_the_uploaded_data_quality_report(
    client: TestClient,
) -> None:
    """Catches structured GX results being omitted from the incident evidence bundle."""
    incident_id, run_id = create_failed_incident(client, "evidence-run-data-quality")
    ingest_pipeline_logs(client, run_id)
    uploaded = client.post(
        f"/api/v1/runs/{run_id}/reports/data-quality",
        json={
            "schema_version": "1.0",
            "contract": {"name": "customer-orders", "version": "1.0.0"},
            "scenario": "range",
            "success": False,
            "summary": {"checks": 1, "passed": 0, "failed": 1},
            "checks": [
                {
                    "id": "validity.amount_range",
                    "dimension": "validity",
                    "success": False,
                    "expectation": "expect_column_values_to_be_between",
                    "expected": {"min_value": 0, "max_value": 10000},
                    "observed": {
                        "unexpected_count": 2,
                        "diagnostic": "api_key=must-not-reach-evidence",
                    },
                }
            ],
            "dataset": {
                "row_count": 20,
                "columns": ["customer_id", "age", "amount"],
            },
            "generated_at": "2026-08-26T14:09:59Z",
        },
    )

    collected = client.post(f"/api/v1/incidents/{incident_id}/collect-evidence")
    items = client.get(f"/api/v1/incidents/{incident_id}/evidence").json()["items"]

    assert uploaded.status_code == 202
    assert collected.status_code == 200
    assert collected.json()["collected_count"] == 3
    report = next(item for item in items if item["evidence_type"] == "DATA_QUALITY_REPORT")
    assert report["source_uri"] == f"dataops://runs/{run_id}/reports/data-quality"
    assert report["metadata"] == {
        "report_id": uploaded.json()["report_id"],
        "run_id": run_id,
        "report_type": "data-quality",
        "checksum": uploaded.json()["checksum"],
        "contract": {"name": "customer-orders", "version": "1.0.0"},
        "scenario": "range",
        "success": False,
        "summary": {"checks": 1, "passed": 0, "failed": 1},
        "redaction_count": 1,
        "collector_redaction_count": 1,
        "truncated": False,
    }
    assert "must-not-reach-evidence" not in report["excerpt"]
    assert "api_key=[REDACTED]" in report["excerpt"]
