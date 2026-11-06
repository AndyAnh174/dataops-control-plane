import importlib.util
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select
from test_rca_agent import FakeLLMClient, FakeRetriever
from test_web_ui import InMemoryLogStore

from dataops_control_plane.api.schemas import DataQualityReportCreate
from dataops_control_plane.domain.models import Incident, PipelineReport, PipelineRun
from dataops_control_plane.main import create_app
from dataops_control_plane.services.rca_agent import RCAAgent


def demo_module():
    path = Path(__file__).resolve().parents[1] / "demo/local/runner.py"
    spec = importlib.util.spec_from_file_location("local_demo_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("scenario,success", [("success", True), ("failure", False)])
def test_local_validator_writes_a_real_consistent_data_quality_report(tmp_path, scenario, success):
    """Catches a demo that logs a claimed failure without actually validating its input."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required")
    module = demo_module()
    run_dir = module.prepare_run(tmp_path, scenario)
    result = subprocess.run([node, "validate.mjs"], cwd=run_dir, capture_output=True, text=True)
    assert result.returncode == (0 if success else 1)
    report = DataQualityReportCreate.model_validate_json(
        (run_dir / "artifacts/data-quality-report.json").read_text()
    )
    assert report.success is success
    assert report.dataset.row_count == 3
    assert report.summary.checks == 2
    assert report.summary.failed == (0 if success else 1)
    if not success:
        assert report.checks[1].observed["invalid_customer_ids"] == [2]


def test_local_runner_does_not_expose_tokens_or_pass_cloud_credentials(
    tmp_path, monkeypatch, capsys
):
    """Catches workflow add-mask commands exposing tokens in an ordinary local terminal."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required")
    module = demo_module()
    fake_agent = tmp_path / "agent.cjs"
    fake_agent.write_text(
        'console.log("::add-mask::"+process.env.DATAOPS_TOKEN);'
        'console.log("value="+process.env.DATAOPS_TOKEN);'
        "console.log(JSON.stringify({hasCloud:!!(process.env.OPENAI_API_KEY||"
        "process.env.VOYAGE_API_KEY||process.env.DATAOPS_LLM_API_KEY||"
        "process.env.DATAOPS_EMBEDDING_API_KEY||process.env.DATAOPS_AGENT_TOKEN)}));"
    )
    monkeypatch.setenv("OPENAI_API_KEY", "private-parent-cloud-key")
    monkeypatch.setenv("VOYAGE_API_KEY", "private-parent-voyage-key")
    monkeypatch.setenv("DATAOPS_AGENT_TOKEN", "private-parent-admin-token")
    result = module.call_agent(
        fake_agent,
        tmp_path,
        "http://127.0.0.1:8000",
        "local-only-integration-token",
        "local/mvp-demo",
        "test-run-1",
    )
    output = capsys.readouterr().out
    assert result == 0
    assert "local-only-integration-token" not in output
    assert "::add-mask::" not in output
    assert '"hasCloud":false' in output


@pytest.mark.parametrize("scenario,status", [("success", "SUCCESS"), ("failure", "FAILED")])
def test_real_portable_agent_reports_to_the_control_plane(tmp_path, scenario, status, capsys):
    """Exercises the real pinned Agent bundle, API auth, SQLite, reports and incidents."""
    bundle = os.environ.get("DATAOPS_TEST_AGENT_BUNDLE")
    if not bundle or not Path(bundle).is_file():
        pytest.skip("Set DATAOPS_TEST_AGENT_BUNDLE for the optional Agent integration test")
    module = demo_module()
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    log_store = InMemoryLogStore()
    retriever, llm = FakeRetriever(), FakeLLMClient()
    app = create_app(
        engine=engine,
        log_store=log_store,
        evidence_sources=[],
        hybrid_retriever=retriever,
        rca_agent=RCAAgent(retriever, llm),
    )
    with TestClient(app, base_url="https://testserver") as client:
        identity = client.post(
            "/api/v1/auth/bootstrap",
            json={
                "email": "demo@example.test",
                "password": "local-only-test-password",
                "workspace_name": "MVP integration",
            },
        ).json()
        project = client.post(
            f"/api/v1/workspaces/{identity['workspaces'][0]['id']}/projects",
            json={
                "name": "Local MVP",
                "provider": "local",
                "project_ref": "local/mvp-demo",
                "default_branch": "main",
            },
        ).json()
        token = client.post(
            f"/api/v1/projects/{project['id']}/tokens",
            json={
                "name": "local-agent",
                "scopes": ["runs:write", "logs:write", "reports:write"],
                "expires_in_days": 1,
            },
        ).json()["token"]

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                response = client.post(
                    self.path,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": self.headers.get("Authorization", ""),
                    },
                )
                self.send_response(response.status_code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(response.content)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        run_dir = module.prepare_run(tmp_path, scenario)
        try:
            exit_code = module.call_agent(
                Path(bundle),
                run_dir,
                f"http://127.0.0.1:{server.server_port}",
                token,
                "local/mvp-demo",
                f"local-{scenario}",
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        assert exit_code == (0 if scenario == "success" else 1)
        with Session(engine) as session:
            run = session.exec(select(PipelineRun)).one()
            assert run.provider == "local"
            assert run.status == status
            assert run.external_run_id == f"local-{scenario}"
            report = session.exec(select(PipelineReport)).one()
            assert report.payload["success"] is (scenario == "success")
            incidents = session.exec(select(Incident)).all()
            assert len(incidents) == (0 if scenario == "success" else 1)
        assert log_store.documents
        assert (run_dir / "trusted-output.json").exists() is (scenario == "success")
        assert token not in capsys.readouterr().out
        assert not llm.calls
