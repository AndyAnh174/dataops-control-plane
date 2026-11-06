"""Resume frozen cloud calls with portable metadata and quota-aware pacing."""

import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


class ClientProcessAdapter:
    def __init__(self, base, *, which=shutil.which):
        self.base, self.which = base, which

    def run(self, command, *args, **kwargs):
        if command and command[0] == "nvidia-smi":
            if self.which("nvidia-smi"):
                result = self.base.run(command, *args, **kwargs)
                if result.returncode == 0 and result.stdout.strip():
                    return result
            description = f"CPU API client ({platform.machine()}, {os.cpu_count()} CPUs); server hardware unknown"
            return subprocess.CompletedProcess(command, 0, stdout=description, stderr="")
        return self.base.run(command, *args, **kwargs)


def verify_frozen_sources(root, identity):
    for name, expected in identity["source_hashes"].items():
        if hashlib.sha256((Path(root) / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Frozen source changed; refuse to resume this study")


def run_cloud(keys, root="/content/hcmute_llm", *, providers=None):
    providers = {"gemini"} if providers is None else set(providers)
    if not providers or not providers <= {"openai", "deepseek", "gemini"}:
        raise ValueError("This is a cloud-only profile; local models require the A100 study")
    root = Path(root)
    identity = json.loads((root / "results/identity.json").read_text())
    verify_frozen_sources(root, identity)
    if (
        hashlib.sha256((root / "data/cases.jsonl").read_bytes()).hexdigest()
        != identity["dataset_sha256"]
    ):
        raise ValueError("Frozen dataset changed")
    sys.path.insert(0, str(root))
    from api_limits import paced_complete

    spec = importlib.util.spec_from_file_location("frozen_log_run", root / "run_logs.py")
    frozen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(frozen)
    phase = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    runtime = root / "results/runtime.json"
    if runtime.exists():
        shutil.copy2(runtime, root / f"results/runtime-before-cloud-{phase}.json")
    note = {
        "phase": phase,
        "client_profile": "portable cloud-only client; local models excluded",
        "providers": sorted(providers),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "machine": platform.machine(),
        "extra_gemini_delay_seconds": 4,
        "effective_gemini_delay": "4 seconds before HTTP plus original 2 seconds after response",
        "timing_note": "Quota waiting is excluded from HTTP latency; this is a later client phase. Actual GPU/CPU metadata is recorded separately.",
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "pacing_sha256": hashlib.sha256((root / "api_limits.py").read_bytes()).hexdigest(),
        "frozen_source_hashes": identity["source_hashes"],
        "dataset_sha256": identity["dataset_sha256"],
    }
    with (root / "results/cloud_resume_sessions.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(note) + "\n")
    original_processes, original_complete = frozen.subprocess, frozen.complete
    frozen.subprocess = ClientProcessAdapter(original_processes)
    frozen.complete = paced_complete(original_complete, {"gemini": 4})
    try:
        summary = frozen.run(keys, str(root), repeats=identity["repeats"], providers=providers)
    finally:
        frozen.subprocess, frozen.complete = original_processes, original_complete
    observed = []
    for row in summary:
        observed.append(
            {
                "model": row["model"],
                "planned_main": 158 * identity["repeats"],
                "all_planned_attempted": row["eligible_for_ranking"],
                "valid_of_planned": row["valid_of_planned"],
                "availability": "complete"
                if row["eligible_for_ranking"]
                else "incomplete_or_unavailable",
                "authority": "derived from case journal; setup-time availability may be historical",
            }
        )
    (root / "results/observed_availability.json").write_text(
        json.dumps(observed, indent=2), encoding="utf-8"
    )
    return summary
