import importlib.util
import json
from pathlib import Path


def shared_module():
    path = Path(__file__).resolve().parents[1] / "shared_runtime.py"
    assert path.exists(), "Shared-runtime notebook execution is not implemented"
    spec = importlib.util.spec_from_file_location("shared_notebook", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_notebook_and_child_scope_do_not_serialize_or_forward_unneeded_keys(tmp_path, monkeypatch):
    module = shared_module()
    monkeypatch.setenv("OPENAI_API_KEY", "private-sentinel")
    notebook = module.build_notebook(tmp_path)
    assert "private-sentinel" not in json.dumps(notebook)
    environment = module.child_environment(
        {
            "OPENAI_API_KEY": "openai-test",
            "VOYAGE_API_KEY": "voyage-test",
            "GEMINI_API_KEY": "gemini-test",
            "DEEPSEEK_API_KEY": "unused-deepseek",
        },
        base={
            "PATH": "/usr/bin",
            "DATAOPS_ADMIN_TOKEN": "unused-admin",
            "CUDA_VISIBLE_DEVICES": "0",
        },
    )
    assert environment["OPENAI_API_KEY"] == "openai-test"
    assert environment["CUDA_VISIBLE_DEVICES"] == "0"
    assert "DEEPSEEK_API_KEY" not in environment and "DATAOPS_ADMIN_TOKEN" not in environment
