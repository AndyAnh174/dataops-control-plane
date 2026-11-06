import importlib.util
import sys
from pathlib import Path

import pytest


def retrieval_module():
    parent = Path(__file__).resolve().parents[1]
    path = parent / "run_retrieval.py"
    assert path.is_file(), "Embedding comparison runner is not implemented"
    sys.path.insert(0, str(parent.parent / "llm"))
    spec = importlib.util.spec_from_file_location("embedding_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_code_query_uses_asymmetric_provider_instruction():
    module = retrieval_module()
    spec = {"provider": "gemini", "model": "gemini-embedding-001", "dimensions": 3072}
    _, _, body = module.embedding_request(spec, ["Find login"], "query", {"GEMINI_API_KEY": "fake"})
    assert body["requests"][0]["taskType"] == "CODE_RETRIEVAL_QUERY"
    _, _, body = module.embedding_request(
        spec, ["def login(): pass"], "document", {"GEMINI_API_KEY": "fake"}
    )
    assert body["requests"][0]["taskType"] == "RETRIEVAL_DOCUMENT"


def test_embedding_response_reorders_and_rejects_duplicate_indices():
    module = retrieval_module()
    spec = {"provider": "openai", "dimensions": 2}
    actual = module.parse_vectors(
        spec, {"data": [{"index": 1, "embedding": [0, 2]}, {"index": 0, "embedding": [3, 4]}]}, 2
    )
    assert actual == [[0.6, 0.8], [0.0, 1.0]]
    with pytest.raises(ValueError):
        module.parse_vectors(
            spec,
            {"data": [{"index": 0, "embedding": [3, 4]}, {"index": 0, "embedding": [0, 2]}]},
            2,
        )


def test_bm25_is_a_real_code_lexical_baseline():
    module = retrieval_module()
    result = module.bm25_rank(
        "expired session token",
        [
            {"id": "irrelevant", "content": "image resize pixels width"},
            {"id": "relevant", "content": "check expired session token then deny login"},
        ],
    )
    assert result[0] == "relevant"


def test_missing_cloud_models_remain_visible_without_fake_zero_quality():
    module = retrieval_module()
    queries = [
        {"id": "q1", "language": "en", "relevant": ["a"]},
        {"id": "q2", "language": "vi", "relevant": ["a"]},
    ]
    rankings = {
        "BM25": [
            {"query_id": "q1", "repeat": 0, "ranking": ["a"]},
            {"query_id": "q2", "repeat": 0, "ranking": ["a"]},
        ]
    }
    result = module.retrieval_summary(rankings, queries, {}, repeats=3)
    assert any(row["model"] == "text-embedding-3-small" for row in result)
    missing = next(row for row in result if row["model"] == "text-embedding-3-small")
    assert not missing["complete"] and missing["ndcg_at_5"] is None
    assert missing["coverage"] == 0


def test_reader_preserves_answer_but_rejects_fabricated_passage_ids():
    module = retrieval_module()
    assert hasattr(module, "reader_choice"), "Reader output validation is not implemented"
    assert module.reader_choice(
        '{"choice":"C0","answer":"Checks the token scope."}', ["C0", "NONE"]
    ) == ("C0", "Checks the token scope.")
    with pytest.raises(ValueError):
        module.reader_choice('{"choice":"C9","answer":"A fake passage"}', ["C0", "NONE"])


def test_gemini_embedding_2_never_aggregates_different_code_samples():
    module = retrieval_module()
    spec = {"provider": "gemini", "model": "gemini-embedding-2", "dimensions": 3072}
    url, _, body = module.embedding_request(
        spec, ["Where is login checked?"], "query", {"GEMINI_API_KEY": "fake"}
    )
    assert url.endswith("gemini-embedding-2:embedContent")
    assert (
        body["content"]["parts"][0]["text"]
        == "task: code retrieval | query: Where is login checked?"
    )
    assert "taskType" not in body
    with pytest.raises(ValueError):
        module.embedding_request(
            spec, ["function one", "function two"], "document", {"GEMINI_API_KEY": "fake"}
        )
