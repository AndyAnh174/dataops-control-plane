import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID

from dataops_control_plane.main import create_app
from dataops_control_plane.services.recovery_execution import RecoveryRequest


@contextmanager
def github_recovery_api() -> Iterator[tuple[str, list[dict[str, object]]]]:
    requests: list[dict[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            requests.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "api_version": self.headers.get("X-GitHub-Api-Version"),
                    "body": json.loads(self.rfile.read(length)),
                }
            )
            self.send_response(204)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_github_recovery_uses_separate_write_token_and_bounded_workflow_inputs(
    monkeypatch,
) -> None:
    """Catches recovery reusing a read token or sending an untraceable workflow dispatch."""
    with github_recovery_api() as (api_url, requests):
        monkeypatch.setenv("DATAOPS_GITHUB_API_URL", api_url)
        monkeypatch.setenv("DATAOPS_GITHUB_TOKEN", "read-only-token")
        monkeypatch.setenv("DATAOPS_GITHUB_RECOVERY_TOKEN", "actions-write-token")
        monkeypatch.setenv("DATAOPS_GITHUB_RECOVERY_WORKFLOW", "dataops-recovery.yml")
        application = create_app()
        executor = application.state.recovery_executor
        request = RecoveryRequest(
            incident_id=UUID("22000000-0000-0000-0000-000000000001"),
            attempt_id=UUID("44000000-0000-0000-0000-000000000001"),
            project_ref="AndyAnh174/dataops-demo-app",
            branch="main",
            action_type="QUARANTINE",
            parameters={"scope": "invalid_rows"},
            idempotency_key="c" * 64,
        )

        dispatch = executor.execute(request)
        executor.close()

    assert executor.provider == "github"
    assert executor.capabilities == frozenset({"RETRY", "QUARANTINE", "ROLLBACK_IMAGE"})
    assert dispatch.external_reference == (
        "github:workflow_dispatch:44000000-0000-0000-0000-000000000001"
    )
    assert len(requests) == 1
    assert requests[0]["path"] == (
        "/repos/AndyAnh174/dataops-demo-app/actions/workflows/dataops-recovery.yml/dispatches"
    )
    assert requests[0]["authorization"] == "Bearer actions-write-token"
    assert requests[0]["body"] == {
        "ref": "main",
        "inputs": {
            "recovery_action": "QUARANTINE",
            "incident_id": "22000000-0000-0000-0000-000000000001",
            "attempt_id": "44000000-0000-0000-0000-000000000001",
            "idempotency_key": "c" * 64,
            "external_reference": ("github:workflow_dispatch:44000000-0000-0000-0000-000000000001"),
            "parameters_json": '{"scope":"invalid_rows"}',
        },
    }
