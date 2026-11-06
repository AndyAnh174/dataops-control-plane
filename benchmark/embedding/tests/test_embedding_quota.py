import importlib.util
import sys
from pathlib import Path


def test_google_budget_counts_utf8_bytes_and_per_input_overhead():
    parent = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(parent.parent / "llm"))
    spec = importlib.util.spec_from_file_location("embedding_quota", parent / "run_retrieval.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "quota_units"), (
        "Conservative embedding quota accounting is not implemented"
    )
    assert module.quota_units(["abc", "đ"]) == 261
