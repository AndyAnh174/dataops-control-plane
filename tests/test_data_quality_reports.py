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


def create_running_run(client: TestClient, external_run_id: str = "dq-run-501") -> str:
    response = client.post(
        "/api/v1/events/pipeline",
        json={
            "event_id": f"github:{external_run_id}:started",
            "event_type": "pipeline.started",
            "occurred_at": "2026-08-26T15:00:00Z",
            "provider": "github",
            "project_ref": "example/customer-pipeline",
            "external_run_id": external_run_id,
            "attempt": 1,
            "commit_sha": "abc1234",
            "branch": "main",
            "status": "RUNNING",
        },
    )
    assert response.status_code == 202
    return response.json()["run_id"]


def data_quality_report(*, scenario: str = "null_rate") -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "contract": {"name": "customer-orders", "version": "1.0.0"},
        "scenario": scenario,
        "success": False,
        "summary": {"checks": 2, "passed": 1, "failed": 1},
        "checks": [
            {
                "id": "schema.required_columns",
                "dimension": "schema",
                "success": True,
                "expectation": "expect_table_columns_to_match_set",
                "expected": ["customer_id", "age", "amount"],
                "observed": ["customer_id", "age", "amount"],
            },
            {
                "id": "completeness.customer_id",
                "dimension": "completeness",
                "success": False,
                "expectation": "expect_column_values_to_not_be_null",
                "expected": {"mostly": 0.99},
                "observed": {"unexpected_count": 4, "unexpected_percent": 20.0},
            },
        ],
        "dataset": {
            "row_count": 20,
            "columns": ["customer_id", "age", "amount"],
        },
        "generated_at": "2026-08-26T15:01:00Z",
    }


def test_report_is_stored_before_a_pipeline_failure_and_retries_are_idempotent(
    client: TestClient,
) -> None:
    """Catches a quality report being lost before the FAILED event creates an Incident."""
    run_id = create_running_run(client)
    payload = data_quality_report()

    first = client.post(f"/api/v1/runs/{run_id}/reports/data-quality", json=payload)
    repeated = client.post(f"/api/v1/runs/{run_id}/reports/data-quality", json=payload)

    assert first.status_code == 202
    assert first.json() == {
        "report_id": first.json()["report_id"],
        "run_id": run_id,
        "checksum": first.json()["checksum"],
        "duplicate": False,
    }
    assert len(first.json()["checksum"]) == 64
    assert repeated.status_code == 202
    assert repeated.json() == {**first.json(), "duplicate": True}


def test_report_contract_rejects_inconsistent_summary(client: TestClient) -> None:
    """Catches an Agent claiming success while individual data checks failed."""
    run_id = create_running_run(client, "dq-run-invalid-summary")
    payload = data_quality_report()
    payload["success"] = True

    response = client.post(f"/api/v1/runs/{run_id}/reports/data-quality", json=payload)

    assert response.status_code == 422


def test_unknown_run_cannot_own_a_data_quality_report(client: TestClient) -> None:
    """Catches orphan reports that cannot later be linked to an Incident."""
    response = client.post(
        "/api/v1/runs/00000000-0000-0000-0000-000000000001/reports/data-quality",
        json=data_quality_report(),
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Pipeline run not found"}


def test_report_payload_is_bounded_before_database_storage(client: TestClient) -> None:
    """Catches a diagnostic value exhausting storage or later model context."""
    run_id = create_running_run(client, "dq-run-oversized")
    payload = data_quality_report()
    payload["checks"][1]["observed"]["diagnostic"] = "x" * 100_000

    response = client.post(f"/api/v1/runs/{run_id}/reports/data-quality", json=payload)

    assert response.status_code == 413
    assert response.json() == {"detail": "Data quality report exceeds 100000 bytes"}
