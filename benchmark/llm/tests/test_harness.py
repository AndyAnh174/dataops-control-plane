"""Contract tests: invalid outputs must not improve benchmark scores."""

import importlib.util
import math
from pathlib import Path

import pytest


def module():
    path = Path(__file__).resolve().parents[1] / "harness.py"
    assert path.is_file(), "LLM benchmark harness is not implemented"
    spec = importlib.util.spec_from_file_location("llm_harness", path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_probabilities_are_validated_without_repair():
    h = module()
    assert (
        h.classification('{"label":"A","probabilities":{"A":0.8,"B":0.2}}', ["A", "B"])[
            "prediction"
        ]
        == "A"
    )
    for text in [
        '{"label":"A","probabilities":{"A":true,"B":0}}',
        '{"label":"A","probabilities":{"A":NaN,"B":0}}',
        '{"label":"C","probabilities":{"A":0.8,"B":0.2}}',
        '{"label":"B","probabilities":{"A":0.8,"B":0.2}}',
        '{"label":"A","probabilities":{"A":0.8,"B":0.8}}',
        '```json\n{"label":"A","probabilities":{"A":1,"B":0}}\n```',
    ]:
        with pytest.raises(ValueError):
            h.classification(text, ["A", "B"])


def test_failures_count_in_quality_and_coverage():
    h = module()
    result = h.classification_metrics(
        [
            {"label": "A", "prediction": "A", "status": "ok", "latency_ms": 10},
            {"label": "B", "status": "provider_error", "latency_ms": 20},
        ],
        ["A", "B"],
    )
    assert result["macro_f1"] == 0.5
    assert result["accuracy"] == 0.5
    assert result["coverage"] == 0.5
    assert result["support"] == {"A": 1, "B": 1}


def test_truncated_provider_responses_are_not_valid():
    h = module()
    with pytest.raises(ValueError):
        h.response_text(
            "openai", {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]}
        )
    with pytest.raises(ValueError):
        h.response_text(
            "gemini",
            {
                "candidates": [
                    {"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "{}"}]}}
                ]
            },
        )


def test_request_keeps_label_out_of_model_input():
    h = module()
    spec = {"provider": "ollama", "model": "qwen3.5:9b"}
    url, headers, body = h.completion_request(spec, "Untrusted example", {"type": "object"}, {})
    assert url == "http://127.0.0.1:11434/api/chat"
    assert body["messages"][-1]["content"] == "Untrusted example"
    assert body["stream"] is False and body["think"] is False
    assert "Authorization" not in headers


def test_resume_refuses_changed_dataset_and_counts_prior_calls(tmp_path):
    h = module()
    journal = h.Journal(tmp_path, {"dataset": "one", "protocol": "frozen"}, cap=2)
    journal.append({"key": "a", "status": "provider_error"})
    resumed = h.Journal(tmp_path, {"dataset": "one", "protocol": "frozen"}, cap=2)
    assert resumed.has("a") and not resumed.has("b")
    resumed.append({"key": "b", "status": "ok"})
    with pytest.raises(RuntimeError):
        resumed.check_budget()
    with pytest.raises(ValueError):
        h.Journal(tmp_path, {"dataset": "changed", "protocol": "frozen"}, cap=2)


def test_retrieval_metrics_use_rank_and_multiple_relevant_chunks():
    h = module()
    result = h.retrieval_metrics(["x", "b", "a"], {"a", "b"}, k=2)
    assert result["recall"] == 0.5
    assert result["mrr"] == 0.5
    assert result["ndcg"] == pytest.approx(0.38685280723454163)
    with pytest.raises(ValueError):
        h.retrieval_metrics(["a", "a"], {"a"}, k=2)


def test_vectors_reject_nan_zero_and_mismatched_dimensions():
    h = module()
    assert h.normalize_vectors([[3, 4], [0, 2]], 2) == [[0.6, 0.8], [0.0, 1.0]]
    for vectors in [[[0, 0]], [[1, math.nan]], [[1, 2, 3]], [[True, 1]]]:
        with pytest.raises(ValueError):
            h.normalize_vectors(vectors, 2)
