import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from elasticsearch import Elasticsearch

from dataops_control_plane.services.elasticsearch_logs import ElasticsearchPipelineLogStore
from dataops_control_plane.services.pipeline_logs import PipelineLogDocument


def test_elasticsearch_store_indexes_deduplicates_and_searches_real_documents() -> None:
    """Catches an adapter that passes unit tests but emits invalid Elasticsearch 9.4 requests."""
    elasticsearch_url = os.getenv("DATAOPS_TEST_ELASTICSEARCH_URL")
    if elasticsearch_url is None:
        pytest.skip("Set DATAOPS_TEST_ELASTICSEARCH_URL to run the Elasticsearch integration test")

    suffix = uuid4().hex
    data_stream_name = f"logs-dataops.pipeline-test{suffix}"
    data_stream_alias = f"logs-dataops.pipeline-test-{suffix}"
    index_template_name = f"dataops-pipeline-logs-test-{suffix}"
    client = Elasticsearch(elasticsearch_url)
    store = ElasticsearchPipelineLogStore(
        client,
        retention="1d",
        data_stream_name=data_stream_name,
        data_stream_alias=data_stream_alias,
        index_template_name=index_template_name,
    )
    run_id = UUID("00000000-0000-0000-0000-000000000123")
    document = PipelineLogDocument(
        event_hash="9" * 64,
        run_id=run_id,
        project_ref="example/data-pipeline",
        provider="github",
        external_run_id="integration-501",
        attempt=1,
        commit_sha="a51e092",
        occurred_at=datetime(2026, 8, 26, 14, 9, 58, tzinfo=UTC),
        ingested_at=datetime(2026, 8, 26, 14, 10, 1, tzinfo=UTC),
        job_name="quality",
        stage="data-quality",
        level="ERROR",
        stream="stderr",
        sequence=7,
        message="Schema validation failed; token=[REDACTED]",
        tags=("ci", "quality"),
        metadata={"table": "customers"},
        redaction_count=1,
    )

    try:
        first = store.append([document])
        repeated = store.append([document])
        client.indices.refresh(index=data_stream_alias)
        found = store.search(
            run_id,
            query="Schema validation",
            stage="data-quality",
            level="ERROR",
            limit=20,
        )
        lifecycle = client.indices.get_data_lifecycle(name=data_stream_name).body

        updated_store = ElasticsearchPipelineLogStore(
            client,
            retention="2d",
            data_stream_name=data_stream_name,
            data_stream_alias=data_stream_alias,
            index_template_name=index_template_name,
        )
        updated_store.ensure_resources()
        updated_lifecycle = client.indices.get_data_lifecycle(name=data_stream_name).body

        assert first.accepted_count == 1
        assert first.duplicate_count == 0
        assert repeated.accepted_count == 0
        assert repeated.duplicate_count == 1
        assert [item.message for item in found] == ["Schema validation failed; token=[REDACTED]"]
        assert lifecycle["data_streams"][0]["lifecycle"]["data_retention"] == "1d"
        assert updated_lifecycle["data_streams"][0]["lifecycle"]["data_retention"] == "2d"
    finally:
        assert data_stream_name.startswith("logs-dataops.pipeline-test")
        assert index_template_name.startswith("dataops-pipeline-logs-test-")
        client.options(ignore_status=404).indices.delete_data_stream(name=data_stream_name)
        client.options(ignore_status=404).indices.delete_index_template(name=index_template_name)
        client.close()
