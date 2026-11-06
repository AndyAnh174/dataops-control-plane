import importlib.util
import json
from pathlib import Path


def report_module():
    path = Path(__file__).resolve().parents[2] / "summarize_studies.py"
    assert path.exists(), "Evidence report exporter is not implemented"
    spec = importlib.util.spec_from_file_location("study_report", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pending_and_completed_event_are_one_attempt(tmp_path):
    module = report_module()
    path = tmp_path / "attempts.jsonl"
    path.write_text(
        json.dumps({"key": "a", "status": "pending"})
        + "\n"
        + json.dumps({"key": "a", "status": "ok"})
        + "\n"
    )
    assert module.load_records(path) == [{"key": "a", "status": "ok"}]


def test_partial_model_cannot_be_declared_a_winner():
    module = report_module()
    result = module.leaders(
        [
            {"model": "partial", "complete": False, "score": 0.99},
            {"model": "complete", "complete": True, "score": 0.7},
            {"model": "unavailable", "complete": False, "score": None},
        ],
        "score",
    )
    assert result == ["complete"]
