import json
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from dataops_control_plane.main import create_app
from dataops_control_plane.services.pipeline_logs import LogWriteResult, PipelineLogDocument


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
        if stage is not None:
            documents = [document for document in documents if document.stage == stage]
        return sorted(documents, key=lambda document: (document.occurred_at, document.sequence))[
            -limit:
        ]


GITHUB_COMMIT_RESPONSE = {
    "sha": "a51e092",
    "node_id": "C_kwDOExample",
    "commit": {
        "author": {
            "name": "Demo Developer",
            "email": "developer@example.com",
            "date": "2026-08-26T14:00:00Z",
        },
        "committer": {
            "name": "Demo Developer",
            "email": "developer@example.com",
            "date": "2026-08-26T14:00:00Z",
        },
        "message": "rename customer identifier",
        "tree": {
            "sha": "tree-a51e092",
            "url": "https://api.github.com/repos/example/customer-pipeline/git/trees/tree-a51e092",
        },
        "url": "https://api.github.com/repos/example/customer-pipeline/git/commits/a51e092",
        "comment_count": 0,
        "verification": {
            "verified": False,
            "reason": "unsigned",
            "signature": None,
            "payload": None,
            "verified_at": None,
        },
    },
    "url": "https://api.github.com/repos/example/customer-pipeline/commits/a51e092",
    "html_url": "https://github.com/example/customer-pipeline/commit/a51e092",
    "comments_url": "https://api.github.com/repos/example/customer-pipeline/commits/a51e092/comments",
    "author": None,
    "committer": None,
    "parents": [],
    "stats": {"total": 5, "additions": 4, "deletions": 1},
    "files": [
        {
            "sha": "file-a51e092",
            "filename": "pipelines/customers.py",
            "status": "modified",
            "additions": 4,
            "deletions": 1,
            "changes": 5,
            "blob_url": "https://github.com/example/customer-pipeline/blob/a51e092/pipelines/customers.py",
            "raw_url": "https://github.com/example/customer-pipeline/raw/a51e092/pipelines/customers.py",
            "contents_url": "https://api.github.com/repos/example/customer-pipeline/contents/pipelines/customers.py?ref=a51e092",
            "patch": "@@ -1 +1 @@\n-customer_id\n+user_id",
        }
    ],
}


@contextmanager
def github_api(
    status_code: int,
    response: dict[str, object],
    *,
    reject_authorization: bool = False,
) -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            rejected = reject_authorization and self.headers.get("Authorization") is not None
            body = json.dumps(
                {"message": "Unexpected authorization"} if rejected else response
            ).encode()
            self.send_response(401 if rejected else status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def build_client(
    github_api_url: str,
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
        create_app(engine=engine, log_store=InMemoryLogStore()),
        raise_server_exceptions=raise_server_exceptions,
    )


def create_incident_with_log(client: TestClient, external_run_id: str) -> str:
    failed = client.post(
        "/api/v1/events/pipeline",
        json={
            "event_id": f"github:{external_run_id}:failed",
            "event_type": "pipeline.completed",
            "occurred_at": "2026-08-26T14:10:00Z",
            "provider": "github",
            "project_ref": "example/customer-pipeline",
            "external_run_id": external_run_id,
            "attempt": 1,
            "commit_sha": "a51e092",
            "branch": "main",
            "status": "FAILED",
            "failed_stage": "data-quality",
        },
    )
    run_id = failed.json()["run_id"]
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
                    "message": "Schema validation failed",
                }
            ]
        },
    )
    listed = client.get("/api/v1/incidents")

    assert failed.status_code == 202
    assert ingested.status_code == 202
    return listed.json()["items"][0]["id"]


def test_github_incident_collects_a_bounded_commit_diff(monkeypatch) -> None:
    """Catches GitHub runs reaching analysis without the change that caused the failure."""
    with github_api(200, GITHUB_COMMIT_RESPONSE) as api_url:
        monkeypatch.setenv("DATAOPS_GITHUB_API_URL", api_url)
        with build_client(api_url) as client:
            incident_id = create_incident_with_log(client, "github-evidence-501")

            collected = client.post(f"/api/v1/incidents/{incident_id}/collect-evidence")
            listed = client.get(f"/api/v1/incidents/{incident_id}/evidence")

    assert collected.status_code == 200
    assert collected.json()["collected_count"] == 3
    assert collected.json()["warnings"] == []
    commit_diff = next(
        item for item in listed.json()["items"] if item["evidence_type"] == "COMMIT_DIFF"
    )
    assert commit_diff["source_uri"] == (
        "https://github.com/example/customer-pipeline/commit/a51e092"
    )
    assert (
        "FILE pipelines/customers.py status=modified additions=4 deletions=1"
        in (commit_diff["excerpt"])
    )
    assert "-customer_id\n+user_id" in commit_diff["excerpt"]
    assert commit_diff["metadata"] == {
        "provider": "github",
        "project_ref": "example/customer-pipeline",
        "commit_sha": "a51e092",
        "changed_files": 1,
        "additions": 4,
        "deletions": 1,
        "truncated": False,
    }


def test_github_api_failure_keeps_core_evidence_and_returns_a_warning(monkeypatch) -> None:
    """Catches a provider outage discarding metadata and logs already available locally."""
    with github_api(503, {"message": "Service unavailable"}) as api_url:
        monkeypatch.setenv("DATAOPS_GITHUB_API_URL", api_url)
        with build_client(api_url) as client:
            incident_id = create_incident_with_log(client, "github-evidence-unavailable")

            collected = client.post(f"/api/v1/incidents/{incident_id}/collect-evidence")
            listed = client.get(f"/api/v1/incidents/{incident_id}/evidence")

    assert collected.status_code == 200
    assert collected.json()["incident_status"] == "ANALYZING"
    assert collected.json()["collected_count"] == 2
    assert collected.json()["warnings"] == [
        {
            "source": "github_commit",
            "code": "SOURCE_UNAVAILABLE",
            "message": "GitHub commit evidence is unavailable (HTTP 503)",
        }
    ]
    assert {item["evidence_type"] for item in listed.json()["items"]} == {
        "PIPELINE_METADATA",
        "LOG_EXCERPT",
    }


def test_github_commit_diff_is_bounded_by_file_count_and_context_size(monkeypatch) -> None:
    """Catches a large commit exhausting database storage or later model context."""
    response = deepcopy(GITHUB_COMMIT_RESPONSE)
    response["files"] = [
        {
            **GITHUB_COMMIT_RESPONSE["files"][0],
            "filename": f"pipelines/file-{index}.py",
            "patch": f"@@ -1 +1 @@\n-legacy-{index}\n+current-{index}\n" + "x" * 2_000,
        }
        for index in range(25)
    ]

    with github_api(200, response) as api_url:
        monkeypatch.setenv("DATAOPS_GITHUB_API_URL", api_url)
        with build_client(api_url) as client:
            incident_id = create_incident_with_log(client, "github-evidence-large-commit")

            client.post(f"/api/v1/incidents/{incident_id}/collect-evidence")
            listed = client.get(f"/api/v1/incidents/{incident_id}/evidence")

    commit_diff = next(
        item for item in listed.json()["items"] if item["evidence_type"] == "COMMIT_DIFF"
    )
    assert len(commit_diff["excerpt"]) <= 20_000
    assert commit_diff["excerpt"].endswith("...[TRUNCATED]")
    assert "pipelines/file-19.py" in commit_diff["excerpt"]
    assert "pipelines/file-20.py" not in commit_diff["excerpt"]
    assert commit_diff["metadata"]["changed_files"] == 25
    assert commit_diff["metadata"]["truncated"] is True


def test_github_connection_failure_is_reported_without_losing_core_evidence(
    monkeypatch,
) -> None:
    """Catches a network failure escaping the provider boundary as an API 500."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    host, port = server.server_address
    server.server_close()
    api_url = f"http://{host}:{port}"
    monkeypatch.setenv("DATAOPS_GITHUB_API_URL", api_url)

    with build_client(api_url, raise_server_exceptions=False) as client:
        incident_id = create_incident_with_log(client, "github-evidence-connection-error")
        collected = client.post(f"/api/v1/incidents/{incident_id}/collect-evidence")
        listed = client.get(f"/api/v1/incidents/{incident_id}/evidence")

    assert collected.status_code == 200
    assert collected.json()["warnings"] == [
        {
            "source": "github_commit",
            "code": "SOURCE_UNAVAILABLE",
            "message": "GitHub commit evidence is unavailable (connection error)",
        }
    ]
    assert len(listed.json()["items"]) == 2


def test_invalid_github_response_is_reported_without_losing_core_evidence(
    monkeypatch,
) -> None:
    """Catches a provider schema change escaping the adapter as an API 500."""
    with github_api(200, {"sha": "a51e092", "files": []}) as api_url:
        monkeypatch.setenv("DATAOPS_GITHUB_API_URL", api_url)
        with build_client(api_url, raise_server_exceptions=False) as client:
            incident_id = create_incident_with_log(client, "github-evidence-invalid-response")
            collected = client.post(f"/api/v1/incidents/{incident_id}/collect-evidence")
            listed = client.get(f"/api/v1/incidents/{incident_id}/evidence")

    assert collected.status_code == 200
    assert collected.json()["warnings"] == [
        {
            "source": "github_commit",
            "code": "INVALID_RESPONSE",
            "message": "GitHub commit evidence returned an invalid response",
        }
    ]
    assert len(listed.json()["items"]) == 2


def test_empty_github_token_does_not_send_an_authorization_header(monkeypatch) -> None:
    """Catches optional Compose configuration emitting an invalid empty Bearer token."""
    with github_api(
        200,
        GITHUB_COMMIT_RESPONSE,
        reject_authorization=True,
    ) as api_url:
        monkeypatch.setenv("DATAOPS_GITHUB_API_URL", api_url)
        monkeypatch.setenv("DATAOPS_GITHUB_TOKEN", "")
        with build_client(api_url) as client:
            incident_id = create_incident_with_log(client, "github-evidence-empty-token")
            collected = client.post(f"/api/v1/incidents/{incident_id}/collect-evidence")

    assert collected.status_code == 200
    assert collected.json()["collected_count"] == 3
    assert collected.json()["warnings"] == []
