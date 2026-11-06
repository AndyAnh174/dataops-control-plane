"""Local MVP launcher. Real Agent, real cloud providers; no VPS or public tunnel."""

import argparse
import getpass
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import sys
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
AGENT_COMMIT = "c1570c5c1d6d3f9e754541c41885dd6ef127f83c"
AGENT_SHA256 = "56bc6db0cb5973621d2f6f11ff519673530388e9f0e3df15eb990f792968306d"
DEFAULT_RUNTIME = ROOT.parent / "dataops-demo-runtime"


def local_url(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("This demo launcher only accepts loopback URLs.")
    return value.rstrip("/")


def ensure_agent(runtime):
    path = runtime / "agent/index.js"
    if not path.exists():
        url = (
            "https://raw.githubusercontent.com/AndyAnh174/dataops-agent/"
            + AGENT_COMMIT
            + "/dist/index.js"
        )
        with urllib.request.urlopen(url, timeout=30) as response:
            data = response.read(2 * 1024 * 1024)
        if hashlib.sha256(data).hexdigest() != AGENT_SHA256:
            raise ValueError("Downloaded Agent integrity check failed.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    if hashlib.sha256(path.read_bytes()).hexdigest() != AGENT_SHA256:
        raise ValueError("Cached Agent integrity check failed; keep it for inspection.")
    return path


def prepare_run(runtime, scenario):
    if scenario not in {"success", "failure"}:
        raise ValueError("Unknown scenario")
    name = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + scenario + "-" + uuid.uuid4().hex[:6]
    run_dir = runtime / "runs" / name
    run_dir.mkdir(parents=True, exist_ok=False)
    for filename in ("dataops.yaml", "validate.mjs", "publish.mjs"):
        shutil.copyfile(HERE / filename, run_dir / filename)
    rows = [
        {"customer_id": 1, "amount": 125},
        {"customer_id": 2, "amount": 125000 if scenario == "failure" else 80},
        {"customer_id": 3, "amount": 42},
    ]
    (run_dir / "rows.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return run_dir


def call_agent(bundle, run_dir, base_url, token, project_ref, run_id):
    node = shutil.which("node")
    if not node:
        raise ValueError("Install Node.js 22 or newer.")
    # A pipeline stage receives its project integration token, never provider/admin credentials.
    allowed = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "HOME",
        "LANG",
        "LC_ALL",
    }
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
        if dirty:
            commit = "local-uncommitted"
    except (OSError, subprocess.CalledProcessError):
        commit = "local-uncommitted"
    env.update(
        DATAOPS_URL=local_url(base_url),
        DATAOPS_TOKEN=token,
        DATAOPS_PROVIDER="local",
        DATAOPS_PROJECT_REF=project_ref,
        DATAOPS_EXTERNAL_RUN_ID=run_id,
        DATAOPS_ATTEMPT="1",
        DATAOPS_BRANCH="local-demo",
        DATAOPS_COMMIT_SHA=commit,
        DATAOPS_JOB_NAME="local-mvp",
        GITHUB_ACTIONS="false",
        GITHUB_WORKSPACE=str(run_dir),
        TEMP=str(run_dir),
        TMP=str(run_dir),
    )
    process = subprocess.Popen(
        [node, str(bundle)],
        cwd=run_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    for line in process.stdout:
        if line.startswith("::add-mask::"):
            continue
        print(line.replace(token, "[MASKED]") if token else line, end="")
    return process.wait()


def start(args):
    runtime = args.runtime_dir.resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    es_url = local_url(args.elasticsearch)
    with urllib.request.urlopen(
        es_url + "/_cluster/health?filter_path=status", timeout=5
    ) as response:
        health = json.load(response)
    if health.get("status") not in {"yellow", "green"}:
        raise ValueError("Elasticsearch is not ready")
    openai_key = os.environ.get("OPENAI_API_KEY") or getpass.getpass("OpenAI API key (hidden): ")
    voyage_key = os.environ.get("VOYAGE_API_KEY") or getpass.getpass("Voyage API key (hidden): ")
    if not openai_key.strip() or not voyage_key.strip():
        raise ValueError("Both OpenAI and Voyage API keys are required.")
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("DATAOPS_") and k not in {"OPENAI_API_KEY", "VOYAGE_API_KEY"}
    }
    env.update(
        PYTHONPATH=str(ROOT / "src"),
        DATAOPS_DATABASE_URL="sqlite:///" + (runtime / "dataops.db").as_posix(),
        DATAOPS_ELASTICSEARCH_URL=es_url,
        DATAOPS_AGENT_TOKEN=secrets.token_urlsafe(32),
        DATAOPS_PUBLIC_URL=f"http://127.0.0.1:{args.port}",
        DATAOPS_WEB_SESSION_COOKIE_SECURE="false",
        DATAOPS_LLM_PROVIDER="openai",
        DATAOPS_LLM_URL="https://api.openai.com/v1",
        DATAOPS_LLM_MODEL="gpt-6-luna",
        DATAOPS_LLM_API_KEY=openai_key.strip(),
        DATAOPS_EMBEDDING_PROVIDER="voyage",
        DATAOPS_EMBEDDING_URL="https://api.voyageai.com/v1",
        DATAOPS_EMBEDDING_MODEL="voyage-4-lite",
        DATAOPS_EMBEDDING_API_KEY=voyage_key.strip(),
        DATAOPS_EMBEDDING_DIMENSIONS="1024",
    )
    if args.verify_cloud:
        code = subprocess.call(
            [sys.executable, "-m", "dataops_control_plane.cloud_check", "--live"],
            cwd=runtime,
            env=env,
        )
        if code:
            return code
    print(f"Open Chrome: http://127.0.0.1:{args.port}/")
    print("SQLite data: " + str(runtime / "dataops.db"))
    print("Keys remain in process memory; they are not written to the repository or runtime files.")
    print(
        "Keep this terminal open. Ctrl+C stops only this API; existing Elasticsearch is untouched."
    )
    return subprocess.call(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "dataops_control_plane.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.port),
        ],
        cwd=runtime,
        env=env,
    )


def agent(args):
    runtime = args.runtime_dir.resolve()
    bundle = ensure_agent(runtime)
    token = getpass.getpass("Project Agent token (hidden): ")
    if not token.strip():
        raise ValueError("A project integration token is required.")
    scenarios = ("success", "failure") if args.scenario == "both" else (args.scenario,)
    for scenario in scenarios:
        run_dir = prepare_run(runtime, scenario)
        run_id = "local-" + run_dir.name
        result = call_agent(bundle, run_dir, args.base_url, token.strip(), args.project_ref, run_id)
        published = (run_dir / "trusted-output.json").exists()
        summary = {
            "scenario": scenario,
            "run_id": run_id,
            "exit_code": result,
            "published": published,
            "artifacts": str(run_dir),
        }
        print(json.dumps(summary))
        if result != (0 if scenario == "success" else 1) or published != (scenario == "success"):
            raise ValueError(
                "Agent scenario did not match the expected result; inspect the output."
            )
    return 0


def analyze(args):
    import httpx

    base = local_url(args.base_url)
    email = input("Local DataOps OWNER/OPERATOR email: ").strip()
    password = getpass.getpass("Local DataOps password (hidden): ")
    with httpx.Client(base_url=base, timeout=600) as client:
        response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        response.raise_for_status()
        password = None
        response = client.get("/api/v1/incidents")
        response.raise_for_status()
        candidates = [
            row
            for row in response.json()["items"]
            if row["pipeline_run"]["provider"] == "local"
            and row["pipeline_run"]["project_ref"] == args.project_ref
        ]
        if args.incident_id:
            candidates = [row for row in candidates if row["id"] == args.incident_id]
        if not candidates:
            raise ValueError("No local incident found; run the failure scenario first.")
        current = max(candidates, key=lambda row: row["created_at"])
        path = "/api/v1/incidents/" + current["id"]
        evidence = client.post(path + "/collect-evidence")
        evidence.raise_for_status()
        if evidence.json()["evidence_count"] < 2:
            raise ValueError("Insufficient direct evidence.")
        document = {
            "document_type": "RUNBOOK",
            "title": "Local customer amount range",
            "content": (
                "The customer contract requires an integer customer_id and amount between 0 and "
                "10000. Inspect failed checks and affected rows. Quarantine only invalid rows, "
                "rerun validation and require operator approval. Never publish unchecked data. "
                "Escalate if evidence is incomplete."
            ),
            "source_uri": "manual://local-mvp/" + args.project_ref + "/amount-range",
            "project_ref": args.project_ref,
            "provider": "local",
            "incident_type": current["pipeline_run"]["failed_stage"],
            "version": "1.0",
        }
        indexed = client.post("/api/v1/retrieval/documents", json=document)
        indexed.raise_for_status()
        report = client.post(path + "/analyze")
        report.raise_for_status()
        result = report.json()
        plan = client.post(path + "/recovery-plans")
        plan.raise_for_status()
        print(
            json.dumps(
                {
                    "incident": current["id"],
                    "analysis_status": result["analysis_status"],
                    "model": result["model_name"],
                    "embedding_model": result["embedding_model"],
                    "duplicate": result["duplicate"],
                    "policy": plan.json()["policy_decision"],
                    "view": base + "/app/incidents/" + current["id"],
                },
                ensure_ascii=False,
            )
        )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "agent", "analyze"])
    parser.add_argument("--runtime-dir", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--elasticsearch", default="http://127.0.0.1:9200")
    parser.add_argument("--verify-cloud", action="store_true")
    parser.add_argument("--scenario", choices=["success", "failure", "both"], default="both")
    parser.add_argument("--project-ref", default="local/mvp-demo")
    parser.add_argument("--incident-id")
    args = parser.parse_args(argv)
    try:
        return {"start": start, "agent": agent, "analyze": analyze}[args.command](args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "http_status": status,
                    "message": str(exc)
                    if status is None
                    else "Request failed; inspect the server error and incident before retrying.",
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
