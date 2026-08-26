import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from elasticsearch import Elasticsearch

from dataops_control_plane.services.elasticsearch_knowledge import ElasticsearchKnowledgeStore
from dataops_control_plane.services.retrieval import KnowledgeDocument, KnowledgeFilter


def _knowledge_document(
    document_id: str,
    *,
    title: str,
    content: str,
    embedding: list[float],
    project_ref: str,
) -> KnowledgeDocument:
    return KnowledgeDocument(
        document_id=document_id,
        checksum=document_id,
        document_type="RUNBOOK",
        title=title,
        content=content,
        source_uri=f"https://example.test/runbooks/{document_id}",
        embedding=embedding,
        embedding_model="test-bge-m3",
        project_ref=project_ref,
        provider="github",
        incident_id=None,
        incident_type="data-quality",
        environment="production",
        version="1.0",
        metadata={"owner": "data-platform"},
        created_at=datetime(2026, 8, 26, tzinfo=UTC),
        updated_at=datetime(2026, 8, 26, tzinfo=UTC),
    )


def test_elasticsearch_knowledge_store_upserts_and_searches_real_vectors_and_bm25() -> None:
    """Catches invalid Elasticsearch 9 dense-vector mappings, queries, aliases, or filters."""
    elasticsearch_url = os.getenv("DATAOPS_TEST_ELASTICSEARCH_URL")
    if elasticsearch_url is None:
        pytest.skip("Set DATAOPS_TEST_ELASTICSEARCH_URL to run the Elasticsearch integration test")

    suffix = uuid4().hex
    index_name = f"knowledge-dataops-test-{suffix}"
    alias_name = f"knowledge-dataops-test-alias-{suffix}"
    template_name = f"dataops-knowledge-test-{suffix}"
    client = Elasticsearch(elasticsearch_url)
    store = ElasticsearchKnowledgeStore(
        client,
        dimensions=3,
        index_name=index_name,
        alias_name=alias_name,
        index_template_name=template_name,
    )
    schema = _knowledge_document(
        "a" * 64,
        title="Schema drift customer_id",
        content="ERR_SCHEMA occurs when customer_id is renamed.",
        embedding=[1.0, 0.0, 0.0],
        project_ref="example/customer-pipeline",
    )
    amount = _knowledge_document(
        "b" * 64,
        title="Amount range",
        content="Quarantine negative amount rows.",
        embedding=[0.0, 1.0, 0.0],
        project_ref="other/project",
    )

    try:
        first = store.upsert(schema)
        updated = store.upsert(schema)
        store.upsert(amount)
        keyword = store.keyword_search(
            "ERR_SCHEMA customer_id",
            filters=KnowledgeFilter(project_ref="example/customer-pipeline"),
            limit=10,
        )
        vector = store.vector_search(
            [0.99, 0.01, 0.0],
            filters=KnowledgeFilter(project_ref="example/customer-pipeline"),
            limit=10,
        )
        filtered = store.vector_search(
            [0.99, 0.01, 0.0],
            filters=KnowledgeFilter(project_ref="other/project"),
            limit=10,
        )

        assert first.result == "created"
        assert updated.result == "updated"
        assert [item.document.document_id for item in keyword] == ["a" * 64]
        assert [item.document.document_id for item in vector] == ["a" * 64]
        assert [item.document.document_id for item in filtered] == ["b" * 64]
        assert vector[0].score > 0.9
    finally:
        assert index_name.startswith("knowledge-dataops-test-")
        assert template_name.startswith("dataops-knowledge-test-")
        client.options(ignore_status=404).indices.delete(index=index_name)
        client.options(ignore_status=404).indices.delete_index_template(name=template_name)
        client.close()
