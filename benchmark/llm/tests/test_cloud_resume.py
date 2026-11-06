import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


def cloud_module():
    path = Path(__file__).resolve().parents[1] / "run_cloud.py"
    assert path.exists(), "Portable cloud resume is not implemented"
    spec = importlib.util.spec_from_file_location("cloud_resume", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cpu_profile_reports_cpu_and_preserves_real_subprocesses():
    module = cloud_module()
    adapter = module.ClientProcessAdapter(subprocess, which=lambda name: None)
    result = adapter.run(["nvidia-smi", "--query-gpu=name"], capture_output=True, text=True)
    assert "CPU" in result.stdout and "server hardware unknown" in result.stdout
    result = adapter.run(
        [sys.executable, "-c", "print('delegated')"], capture_output=True, text=True
    )
    assert result.stdout.strip() == "delegated"


def test_resume_rejects_any_frozen_source_change(tmp_path):
    module = cloud_module()
    path = tmp_path / "frozen.py"
    path.write_text("original")
    identity = {"source_hashes": {"frozen.py": hashlib.sha256(path.read_bytes()).hexdigest()}}
    module.verify_frozen_sources(tmp_path, identity)
    path.write_text("changed after results")
    with pytest.raises(ValueError):
        module.verify_frozen_sources(tmp_path, identity)


def test_cloud_profile_rejects_local_model_execution_before_loading_files(tmp_path):
    module = cloud_module()
    with pytest.raises(ValueError, match="cloud"):
        module.run_cloud({}, tmp_path, providers={"ollama"})
