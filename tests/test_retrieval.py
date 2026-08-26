import math
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from dataops_control_plane.main import create_app
from dataops_control_plane.services.retrieval import (
    EmbeddingUnavailable,
    HybridRetriever,
    KnowledgeDocument,
    KnowledgeFilter,
    KnowledgeSearchCandidate,
    KnowledgeWriteResult,
    reciprocal_rank_fusion,
)


class DeterministicEmbeddingProvider:
    model_name = "test-bge-m3"
    dimensions = 3

    def __init__(self) -> None:
        self.embedded_texts: list[str] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        embeddings: list[list[float]] = []
        for text in texts:
            self.embedded_texts.append(text)
            if "leaked-secret" in text:
                raise AssertionError("Unredacted content reached the embedding provider")
            lowered = text.casefold()
            if "amount" in lowered or "range" in lowered:
                embeddings.append([1.0, 0.0, 0.0])
            elif "schema" in lowered or "column" in lowered:
                embeddings.append([0.0, 1.0, 0.0])
            else:
                embeddings.append([0.0, 0.0, 1.0])
        return embeddings

    def close(self) -> None:
        pass


class UnavailableEmbeddingProvider(DeterministicEmbeddingProvider):
    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise EmbeddingUnavailable("Embedding service is temporarily unavailable")


class InMemoryKnowledgeStore:
    def __init__(self) -> None:
        self.documents: dict[str, KnowledgeDocument] = {}

    def upsert(self, document: KnowledgeDocument) -> KnowledgeWriteResult:
        result = "updated" if document.document_id in self.documents else "created"
        self.documents[document.document_id] = replace(document)
        return KnowledgeWriteResult(result=result)

    def keyword_search(
        self,
        query: str,
        *,
        filters: KnowledgeFilter,
        limit: int,
    ) -> list[KnowledgeSearchCandidate]:
        query_terms = set(query.casefold().split())
        matches: list[KnowledgeSearchCandidate] = []
        for document in self.documents.values():
            if not _matches_filter(document, filters):
                continue
            content_terms = (document.title + " " + document.content).casefold().split()
            score = float(sum(term.strip(".,:;()") in query_terms for term in content_terms))
            if score > 0:
                matches.append(KnowledgeSearchCandidate(document=document, score=score))
        return sorted(matches, key=lambda item: (-item.score, item.document.document_id))[:limit]

    def vector_search(
        self,
        embedding: Sequence[float],
        *,
        filters: KnowledgeFilter,
        limit: int,
    ) -> list[KnowledgeSearchCandidate]:
        matches = [
            KnowledgeSearchCandidate(
                document=document,
                score=_cosine_similarity(embedding, document.embedding),
            )
            for document in self.documents.values()
            if _matches_filter(document, filters)
        ]
        return sorted(matches, key=lambda item: (-item.score, item.document.document_id))[:limit]

    def close(self) -> None:
        pass


class EmptyLogStore:
    def append(self, documents):
        raise AssertionError("Log append is not used by this test")

    def search(self, run_id, *, query, stage, level, limit):
        return []


def _matches_filter(document: KnowledgeDocument, filters: KnowledgeFilter) -> bool:
    if filters.project_ref is not None and document.project_ref != filters.project_ref:
        return False
    if filters.document_types and document.document_type not in filters.document_types:
        return False
    if filters.provider is not None and document.provider != filters.provider:
        return False
    if filters.incident_type is not None and document.incident_type != filters.incident_type:
        return False
    if filters.environment is not None and document.environment != filters.environment:
        return False
    return filters.created_after is None or document.created_at >= filters.created_after


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    return numerator / (left_norm * right_norm)


def _document(document_id: str, title: str) -> KnowledgeDocument:
    return KnowledgeDocument(
        document_id=document_id,
        checksum=document_id * 8,
        document_type="RUNBOOK",
        title=title,
        content=f"Content for {title}",
        source_uri=f"https://example.test/{document_id}",
        embedding=[1.0, 0.0, 0.0],
        embedding_model="test-bge-m3",
        project_ref="example/customer-pipeline",
        provider="github",
        incident_id=None,
        incident_type="data-quality",
        environment="production",
        version="1.0",
        metadata={},
        created_at=datetime(2026, 8, 26, tzinfo=UTC),
        updated_at=datetime(2026, 8, 26, tzinfo=UTC),
    )


def test_rrf_rewards_a_document_found_by_both_retrievers_and_keeps_diagnostics() -> None:
    """Catches fusion that compares incomparable BM25 and cosine scores directly."""
    exact = _document("a" * 64, "Exact error code")
    both = _document("b" * 64, "Relevant in both branches")
    semantic = _document("c" * 64, "Semantic match")

    results = reciprocal_rank_fusion(
        keyword_results=[
            KnowledgeSearchCandidate(document=exact, score=12.0),
            KnowledgeSearchCandidate(document=both, score=5.0),
        ],
        vector_results=[
            KnowledgeSearchCandidate(document=both, score=0.95),
            KnowledgeSearchCandidate(document=semantic, score=0.90),
        ],
        rank_constant=60,
    )

    assert [result.document.document_id for result in results] == [
        "b" * 64,
        "a" * 64,
        "c" * 64,
    ]
    assert results[0].rrf_score == pytest.approx(1 / 62 + 1 / 61)
    assert results[0].keyword_rank == 2
    assert results[0].keyword_score == 5.0
    assert results[0].vector_rank == 1
    assert results[0].vector_score == 0.95
    assert results[0].matched_by == ("keyword", "vector")


def test_long_document_keeps_full_bm25_text_but_bounds_the_embedding_input() -> None:
    """Catches a valid document exceeding BGE-M3 context when truncate is disabled."""
    store = InMemoryKnowledgeStore()
    embedder = DeterministicEmbeddingProvider()
    retriever = HybridRetriever(store, embedder)
    content = "amount range " + ("middle-evidence " * 1_500) + "schema drift at the end"

    result = retriever.index_document(
        document_type="INCIDENT_SUMMARY",
        title="Long incident",
        content=content,
        source_uri="dataops://incidents/long-test",
    )

    assert result.document.content == content
    assert store.documents[result.document.document_id].content == content
    assert len(embedder.embedded_texts) == 1
    assert len(embedder.embedded_texts[0]) <= 1_800
    assert "amount range" in embedder.embedded_texts[0]
    assert "schema drift at the end" in embedder.embedded_texts[0]


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATAOPS_AGENT_TOKEN", "retrieval-test-token")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    retriever = HybridRetriever(InMemoryKnowledgeStore(), DeterministicEmbeddingProvider())
    with TestClient(
        create_app(
            engine=engine,
            log_store=EmptyLogStore(),
            evidence_sources=[],
            hybrid_retriever=retriever,
        )
    ) as test_client:
        yield test_client


def _auth() -> dict[str, str]:
    return {"Authorization": "Bearer retrieval-test-token"}


def test_api_redacts_before_embedding_and_filters_results_by_project(client: TestClient) -> None:
    """Catches secrets reaching Ollama or cross-project knowledge leaking into retrieval."""
    first = client.post(
        "/api/v1/retrieval/documents",
        headers=_auth(),
        json={
            "document_type": "RUNBOOK",
            "title": "Amount range violation",
            "content": "Quarantine rows outside the amount range; api_key=leaked-secret",
            "source_uri": "https://ci-user:url-secret@example.test/runbooks/amount-range",
            "project_ref": "example/customer-pipeline",
            "provider": "github",
            "incident_type": "data-quality",
            "environment": "production",
            "version": "1.0",
            "metadata": {"owner": "data-platform", "api_key": "metadata-secret"},
        },
    )
    other_project = client.post(
        "/api/v1/retrieval/documents",
        headers=_auth(),
        json={
            "document_type": "RUNBOOK",
            "title": "Amount range for another team",
            "content": "This document must be filtered out.",
            "source_uri": "https://example.test/runbooks/other-team",
            "project_ref": "other/project",
        },
    )

    response = client.post(
        "/api/v1/retrieval/search",
        headers=_auth(),
        json={
            "query": "amount exceeds accepted range",
            "top_k": 5,
            "filters": {
                "project_ref": "example/customer-pipeline",
                "document_types": ["RUNBOOK"],
            },
        },
    )

    assert first.status_code == 202
    assert first.json()["result"] == "created"
    assert first.json()["redaction_count"] == 3
    assert other_project.status_code == 202
    assert response.status_code == 200
    assert response.json()["embedding_model"] == "test-bge-m3"
    assert response.json()["fusion"] == {
        "method": "rrf",
        "rank_constant": 60,
        "candidate_limit": 15,
    }
    assert len(response.json()["items"]) == 1
    item = response.json()["items"][0]
    assert item["title"] == "Amount range violation"
    assert "leaked-secret" not in response.text
    assert "url-secret" not in response.text
    assert "metadata-secret" not in response.text
    assert "api_key=[REDACTED]" in item["content"]
    assert item["source_uri"] == "https://ci-user:[REDACTED]@example.test/runbooks/amount-range"
    assert item["metadata"]["api_key"] == "[REDACTED]"
    assert item["matched_by"] == ["keyword", "vector"]
    assert item["keyword_rank"] == 1
    assert item["vector_rank"] == 1


def test_incident_evidence_can_be_indexed_as_one_bounded_summary(client: TestClient) -> None:
    """Catches M4 embedding raw logs individually or losing the current Incident linkage."""
    failed = client.post(
        "/api/v1/events/pipeline",
        headers=_auth(),
        json={
            "event_id": "github:retrieval-incident:failed",
            "event_type": "pipeline.completed",
            "occurred_at": "2026-08-26T17:25:59Z",
            "provider": "github",
            "project_ref": "example/customer-pipeline",
            "external_run_id": "retrieval-incident-501",
            "attempt": 1,
            "commit_sha": "f2b8e56",
            "branch": "main",
            "status": "FAILED",
            "failed_stage": "data-quality",
        },
    )
    report = client.post(
        f"/api/v1/runs/{failed.json()['run_id']}/reports/data-quality",
        headers=_auth(),
        json={
            "schema_version": "1.0",
            "contract": {"name": "customer-orders", "version": "1.0.0"},
            "scenario": "range",
            "success": False,
            "summary": {"checks": 1, "passed": 0, "failed": 1},
            "checks": [
                {
                    "id": "validity.amount_range",
                    "dimension": "validity",
                    "success": False,
                    "expectation": "expect_column_values_to_be_between",
                    "expected": {"min_value": 0, "max_value": 10000},
                    "observed": {"unexpected_count": 2},
                }
            ],
            "dataset": {
                "row_count": 200,
                "columns": ["customer_id", "age", "amount"],
            },
            "generated_at": "2026-08-26T17:25:58Z",
        },
    )
    incident_id = client.get("/api/v1/incidents").json()["items"][0]["id"]
    collected = client.post(
        f"/api/v1/incidents/{incident_id}/collect-evidence",
        headers=_auth(),
    )

    indexed = client.post(
        f"/api/v1/incidents/{incident_id}/index-knowledge",
        headers=_auth(),
    )
    found = client.post(
        "/api/v1/retrieval/search",
        headers=_auth(),
        json={
            "query": "amount range data quality failure",
            "filters": {"project_ref": "example/customer-pipeline"},
        },
    )

    assert report.status_code == 202
    assert collected.status_code == 200
    assert indexed.status_code == 202
    assert indexed.json()["document_type"] == "INCIDENT_SUMMARY"
    assert found.status_code == 200
    incident_result = next(
        item for item in found.json()["items"] if item["document_type"] == "INCIDENT_SUMMARY"
    )
    assert incident_result["incident_id"] == incident_id
    assert "validity.amount_range" in incident_result["content"]
    assert len(incident_result["content"]) <= 20_000


def test_embedding_outage_is_a_bounded_service_unavailable_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches an Ollama outage becoming an unhandled 500 or corrupting stored knowledge."""
    monkeypatch.setenv("DATAOPS_AGENT_TOKEN", "retrieval-test-token")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    retriever = HybridRetriever(InMemoryKnowledgeStore(), UnavailableEmbeddingProvider())
    with TestClient(
        create_app(
            engine=engine,
            log_store=EmptyLogStore(),
            evidence_sources=[],
            hybrid_retriever=retriever,
        ),
        raise_server_exceptions=False,
    ) as client:
        response = client.post(
            "/api/v1/retrieval/search",
            headers=_auth(),
            json={"query": "schema drift", "top_k": 5},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Embedding service is temporarily unavailable"}
