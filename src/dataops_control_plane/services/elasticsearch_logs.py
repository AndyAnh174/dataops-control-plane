from collections.abc import Mapping, Sequence
from datetime import datetime
from threading import Lock
from typing import Any
from uuid import UUID

from elasticsearch import ApiError, BadRequestError, Elasticsearch, TransportError
from elasticsearch.helpers import streaming_bulk

from dataops_control_plane.config import Settings
from dataops_control_plane.services.pipeline_logs import (
    LogStoreUnavailable,
    LogWriteResult,
    PipelineLogDocument,
)

INDEX_TEMPLATE_NAME = "dataops-pipeline-logs-v1"
DATA_STREAM_NAME = "logs-dataops.pipeline-v1"
DATA_STREAM_ALIAS = "logs-dataops.pipeline"

PIPELINE_LOG_MAPPINGS: dict[str, Any] = {
    "dynamic": "strict",
    "date_detection": False,
    "properties": {
        "@timestamp": {"type": "date"},
        "ingested_at": {"type": "date"},
        "data_stream": {
            "properties": {
                "type": {"type": "constant_keyword", "value": "logs"},
                "dataset": {"type": "constant_keyword", "value": "dataops.pipeline"},
                "namespace": {"type": "constant_keyword", "value": "v1"},
            }
        },
        "event": {
            "properties": {
                "dataset": {"type": "constant_keyword", "value": "dataops.pipeline"},
            }
        },
        "event_hash": {"type": "keyword", "ignore_above": 64},
        "run_id": {"type": "keyword", "ignore_above": 64},
        "project_id": {"type": "keyword", "ignore_above": 255},
        "project_ref": {"type": "keyword", "ignore_above": 255},
        "provider": {"type": "keyword", "ignore_above": 64},
        "external_run_id": {"type": "keyword", "ignore_above": 255},
        "attempt": {"type": "integer"},
        "commit_sha": {"type": "keyword", "ignore_above": 128},
        "job_name": {"type": "keyword", "ignore_above": 255},
        "stage": {"type": "keyword", "ignore_above": 255},
        "level": {"type": "keyword", "ignore_above": 16},
        "stream": {"type": "keyword", "ignore_above": 16},
        "sequence": {"type": "long"},
        "message": {"type": "match_only_text"},
        "stack_trace": {"type": "match_only_text"},
        "tags": {"type": "keyword", "ignore_above": 255},
        "metadata": {"type": "flattened"},
        "redaction_count": {"type": "integer"},
    },
}


class ElasticsearchPipelineLogStore:
    def __init__(
        self,
        client: Elasticsearch,
        *,
        retention: str = "30d",
        data_stream_name: str = DATA_STREAM_NAME,
        data_stream_alias: str = DATA_STREAM_ALIAS,
        index_template_name: str = INDEX_TEMPLATE_NAME,
    ) -> None:
        self._client = client
        self._retention = retention
        self._data_stream_name = data_stream_name
        self._data_stream_alias = data_stream_alias
        self._index_template_name = index_template_name
        self._resources_ready = False
        self._resource_lock = Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> "ElasticsearchPipelineLogStore":
        client_options: dict[str, Any] = {
            "request_timeout": 10,
            "retry_on_timeout": True,
            "max_retries": 2,
        }
        if settings.elasticsearch_api_key is not None:
            client_options["api_key"] = settings.elasticsearch_api_key.get_secret_value()
        elif settings.elasticsearch_username and settings.elasticsearch_password is not None:
            client_options["basic_auth"] = (
                settings.elasticsearch_username,
                settings.elasticsearch_password.get_secret_value(),
            )

        if settings.elasticsearch_url.lower().startswith("https://"):
            client_options["verify_certs"] = settings.elasticsearch_verify_certs
            if settings.elasticsearch_ca_certs is not None:
                client_options["ca_certs"] = settings.elasticsearch_ca_certs

        return cls(
            Elasticsearch(settings.elasticsearch_url, **client_options),
            retention=settings.elasticsearch_log_retention,
        )

    def ensure_resources(self) -> None:
        if self._resources_ready:
            return

        with self._resource_lock:
            if self._resources_ready:
                return
            try:
                self._client.indices.put_index_template(
                    name=self._index_template_name,
                    index_patterns=[self._data_stream_name],
                    priority=501,
                    data_stream={},
                    template={
                        "lifecycle": {"data_retention": self._retention},
                        "mappings": PIPELINE_LOG_MAPPINGS,
                    },
                    meta={
                        "description": "Version 1 mapping for DataOps pipeline logs",
                        "managed_by": "dataops-control-plane",
                    },
                )
                try:
                    self._client.indices.create_data_stream(name=self._data_stream_name)
                except BadRequestError as exc:
                    if exc.error != "resource_already_exists_exception":
                        raise
                self._client.indices.put_data_lifecycle(
                    name=self._data_stream_name,
                    data_retention=self._retention,
                )
                self._client.indices.update_aliases(
                    actions=[
                        {
                            "add": {
                                "index": self._data_stream_name,
                                "alias": self._data_stream_alias,
                                "is_write_index": True,
                            }
                        }
                    ]
                )
            except (ApiError, TransportError) as exc:
                raise LogStoreUnavailable(
                    "Unable to initialize Elasticsearch log resources"
                ) from exc
            self._resources_ready = True

    def append(self, documents: Sequence[PipelineLogDocument]) -> LogWriteResult:
        self.ensure_resources()
        accepted_count = 0
        duplicate_count = 0
        actions = (
            {
                "_op_type": "create",
                "_index": self._data_stream_alias,
                "_id": document.event_hash,
                "_source": _document_source(document),
            }
            for document in documents
        )

        try:
            results = streaming_bulk(
                self._client,
                actions,
                chunk_size=500,
                raise_on_error=False,
                ignore_status=(409,),
            )
            for ok, item in results:
                result = next(iter(item.values()))
                response_status = int(result.get("status", 0))
                if ok:
                    accepted_count += 1
                elif response_status == 409:
                    duplicate_count += 1
                else:
                    reason = result.get("error", "unknown bulk indexing error")
                    raise LogStoreUnavailable(f"Elasticsearch rejected a pipeline log: {reason}")
        except (ApiError, TransportError) as exc:
            raise LogStoreUnavailable("Unable to write pipeline logs to Elasticsearch") from exc

        return LogWriteResult(
            accepted_count=accepted_count,
            duplicate_count=duplicate_count,
        )

    def search(
        self,
        run_id: UUID,
        *,
        query: str | None,
        stage: str | None,
        level: str | None,
        limit: int,
    ) -> list[PipelineLogDocument]:
        self.ensure_resources()
        filters: list[dict[str, Any]] = [{"term": {"run_id": str(run_id)}}]
        if stage is not None:
            filters.append({"term": {"stage": stage}})
        if level is not None:
            filters.append({"term": {"level": level}})

        bool_query: dict[str, Any] = {"filter": filters}
        if query is not None:
            bool_query["must"] = [{"match": {"message": {"query": query, "operator": "and"}}}]

        try:
            response = self._client.search(
                index=self._data_stream_alias,
                query={"bool": bool_query},
                sort=[
                    {"@timestamp": {"order": "desc"}},
                    {"sequence": {"order": "desc"}},
                ],
                size=limit,
            )
        except (ApiError, TransportError) as exc:
            raise LogStoreUnavailable("Unable to search pipeline logs in Elasticsearch") from exc

        hits = response.body["hits"]["hits"]
        documents = [_source_document(hit["_source"]) for hit in hits]
        documents.reverse()
        return documents

    def close(self) -> None:
        self._client.close()


def _document_source(document: PipelineLogDocument) -> dict[str, Any]:
    source: dict[str, Any] = {
        "@timestamp": document.occurred_at.isoformat(),
        "ingested_at": document.ingested_at.isoformat(),
        "data_stream": {"type": "logs", "dataset": "dataops.pipeline", "namespace": "v1"},
        "event": {"dataset": "dataops.pipeline"},
        "event_hash": document.event_hash,
        "run_id": str(document.run_id),
        "project_ref": document.project_ref,
        "provider": document.provider,
        "external_run_id": document.external_run_id,
        "attempt": document.attempt,
        "commit_sha": document.commit_sha,
        "job_name": document.job_name,
        "stage": document.stage,
        "level": document.level,
        "stream": document.stream,
        "sequence": document.sequence,
        "message": document.message,
        "tags": list(document.tags),
        "metadata": document.metadata,
        "redaction_count": document.redaction_count,
    }
    if document.project_id is not None:
        source["project_id"] = document.project_id
    if document.stack_trace is not None:
        source["stack_trace"] = document.stack_trace
    return source


def _source_document(source: Mapping[str, Any]) -> PipelineLogDocument:
    return PipelineLogDocument(
        event_hash=str(source["event_hash"]),
        run_id=UUID(str(source["run_id"])),
        project_id=_optional_string(source.get("project_id")),
        project_ref=str(source["project_ref"]),
        provider=str(source["provider"]),
        external_run_id=str(source["external_run_id"]),
        attempt=int(source["attempt"]),
        commit_sha=str(source["commit_sha"]),
        occurred_at=datetime.fromisoformat(str(source["@timestamp"])),
        ingested_at=datetime.fromisoformat(str(source["ingested_at"])),
        job_name=str(source["job_name"]),
        stage=str(source["stage"]),
        level=str(source["level"]),
        stream=str(source["stream"]),
        sequence=int(source["sequence"]),
        message=str(source["message"]),
        stack_trace=_optional_string(source.get("stack_trace")),
        tags=tuple(str(tag) for tag in source.get("tags", [])),
        metadata=dict(source.get("metadata", {})),
        redaction_count=int(source.get("redaction_count", 0)),
    )


def _optional_string(value: object) -> str | None:
    return None if value is None else str(value)
