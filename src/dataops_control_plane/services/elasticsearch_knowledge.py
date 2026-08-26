from collections.abc import Mapping, Sequence
from datetime import datetime
from threading import Lock
from typing import Any
from uuid import UUID

from elasticsearch import ApiError, BadRequestError, Elasticsearch, TransportError

from dataops_control_plane.config import Settings
from dataops_control_plane.services.retrieval import (
    KnowledgeDocument,
    KnowledgeFilter,
    KnowledgeSearchCandidate,
    KnowledgeStoreUnavailable,
    KnowledgeWriteResult,
)

INDEX_TEMPLATE_NAME = "dataops-knowledge-v1"
INDEX_NAME = "knowledge-dataops-v1"
INDEX_ALIAS = "knowledge-dataops"


def knowledge_mappings(dimensions: int) -> dict[str, Any]:
    return {
        "dynamic": "strict",
        "date_detection": False,
        "_source": {"excludes": ["embedding"]},
        "properties": {
            "document_id": {"type": "keyword", "ignore_above": 64},
            "checksum": {"type": "keyword", "ignore_above": 64},
            "document_type": {"type": "keyword", "ignore_above": 64},
            "title": {"type": "text"},
            "content": {"type": "text"},
            "source_uri": {"type": "keyword", "ignore_above": 2048},
            "embedding": {
                "type": "dense_vector",
                "dims": dimensions,
                "index": True,
                "similarity": "cosine",
            },
            "embedding_model": {"type": "keyword", "ignore_above": 128},
            "project_ref": {"type": "keyword", "ignore_above": 255},
            "provider": {"type": "keyword", "ignore_above": 64},
            "incident_id": {"type": "keyword", "ignore_above": 64},
            "incident_type": {"type": "keyword", "ignore_above": 128},
            "environment": {"type": "keyword", "ignore_above": 128},
            "version": {"type": "keyword", "ignore_above": 128},
            "metadata": {"type": "flattened"},
            "created_at": {"type": "date"},
            "updated_at": {"type": "date"},
        },
    }


class ElasticsearchKnowledgeStore:
    def __init__(
        self,
        client: Elasticsearch,
        *,
        dimensions: int,
        index_name: str = INDEX_NAME,
        alias_name: str = INDEX_ALIAS,
        index_template_name: str = INDEX_TEMPLATE_NAME,
    ) -> None:
        self._client = client
        self._dimensions = dimensions
        self._index_name = index_name
        self._alias_name = alias_name
        self._index_template_name = index_template_name
        self._resources_ready = False
        self._resource_lock = Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> "ElasticsearchKnowledgeStore":
        client_options: dict[str, Any] = {
            "request_timeout": 15,
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
            dimensions=settings.embedding_dimensions,
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
                    index_patterns=[self._index_name],
                    priority=501,
                    template={"mappings": knowledge_mappings(self._dimensions)},
                    meta={
                        "description": "Version 1 mapping for DataOps retrieval knowledge",
                        "managed_by": "dataops-control-plane",
                    },
                )
                try:
                    self._client.indices.create(index=self._index_name)
                except BadRequestError as exc:
                    if exc.error != "resource_already_exists_exception":
                        raise
                self._client.indices.update_aliases(
                    actions=[
                        {
                            "add": {
                                "index": self._index_name,
                                "alias": self._alias_name,
                                "is_write_index": True,
                            }
                        }
                    ]
                )
            except (ApiError, TransportError) as exc:
                raise KnowledgeStoreUnavailable(
                    "Knowledge storage is temporarily unavailable"
                ) from exc
            self._resources_ready = True

    def upsert(self, document: KnowledgeDocument) -> KnowledgeWriteResult:
        self.ensure_resources()
        try:
            response = self._client.index(
                index=self._alias_name,
                id=document.document_id,
                document=_document_source(document),
                refresh="wait_for",
            )
        except (ApiError, TransportError) as exc:
            raise KnowledgeStoreUnavailable("Knowledge storage is temporarily unavailable") from exc
        return KnowledgeWriteResult(result=str(response.body["result"]))

    def keyword_search(
        self,
        query: str,
        *,
        filters: KnowledgeFilter,
        limit: int,
    ) -> list[KnowledgeSearchCandidate]:
        self.ensure_resources()
        bool_query: dict[str, Any] = {
            "must": [
                {
                    "multi_match": {
                        "query": query,
                        "fields": ["title^3", "content"],
                    }
                }
            ]
        }
        filter_queries = _filter_queries(filters)
        if filter_queries:
            bool_query["filter"] = filter_queries
        try:
            response = self._client.search(
                index=self._alias_name,
                query={"bool": bool_query},
                size=limit,
            )
        except (ApiError, TransportError) as exc:
            raise KnowledgeStoreUnavailable("Knowledge storage is temporarily unavailable") from exc
        return _search_candidates(response.body["hits"]["hits"])

    def vector_search(
        self,
        embedding: Sequence[float],
        *,
        filters: KnowledgeFilter,
        limit: int,
    ) -> list[KnowledgeSearchCandidate]:
        self.ensure_resources()
        knn: dict[str, Any] = {
            "field": "embedding",
            "query_vector": list(embedding),
            "k": limit,
            "num_candidates": min(max(limit * 3, 50), 10_000),
        }
        filter_queries = _filter_queries(filters)
        if filter_queries:
            knn["filter"] = {"bool": {"filter": filter_queries}}
        try:
            response = self._client.search(
                index=self._alias_name,
                knn=knn,
                size=limit,
            )
        except (ApiError, TransportError) as exc:
            raise KnowledgeStoreUnavailable("Knowledge storage is temporarily unavailable") from exc
        return _search_candidates(response.body["hits"]["hits"])

    def close(self) -> None:
        self._client.close()


def _filter_queries(filters: KnowledgeFilter) -> list[dict[str, Any]]:
    queries: list[dict[str, Any]] = []
    if filters.project_ref is not None:
        queries.append({"term": {"project_ref": filters.project_ref}})
    if filters.document_types:
        queries.append({"terms": {"document_type": list(filters.document_types)}})
    if filters.provider is not None:
        queries.append({"term": {"provider": filters.provider}})
    if filters.incident_type is not None:
        queries.append({"term": {"incident_type": filters.incident_type}})
    if filters.environment is not None:
        queries.append({"term": {"environment": filters.environment}})
    if filters.created_after is not None:
        queries.append({"range": {"created_at": {"gte": filters.created_after.isoformat()}}})
    return queries


def _document_source(document: KnowledgeDocument) -> dict[str, Any]:
    source: dict[str, Any] = {
        "document_id": document.document_id,
        "checksum": document.checksum,
        "document_type": document.document_type,
        "title": document.title,
        "content": document.content,
        "source_uri": document.source_uri,
        "embedding": document.embedding,
        "embedding_model": document.embedding_model,
        "metadata": document.metadata,
        "created_at": document.created_at.isoformat(),
        "updated_at": document.updated_at.isoformat(),
    }
    optional = {
        "project_ref": document.project_ref,
        "provider": document.provider,
        "incident_id": str(document.incident_id) if document.incident_id is not None else None,
        "incident_type": document.incident_type,
        "environment": document.environment,
        "version": document.version,
    }
    source.update({key: value for key, value in optional.items() if value is not None})
    return source


def _search_candidates(hits: Sequence[Mapping[str, Any]]) -> list[KnowledgeSearchCandidate]:
    return [
        KnowledgeSearchCandidate(
            document=_source_document(hit["_source"]),
            score=float(hit["_score"]),
        )
        for hit in hits
    ]


def _source_document(source: Mapping[str, Any]) -> KnowledgeDocument:
    incident_id = source.get("incident_id")
    return KnowledgeDocument(
        document_id=str(source["document_id"]),
        checksum=str(source["checksum"]),
        document_type=str(source["document_type"]),
        title=str(source["title"]),
        content=str(source["content"]),
        source_uri=str(source["source_uri"]),
        embedding=[],
        embedding_model=str(source["embedding_model"]),
        project_ref=_optional_string(source.get("project_ref")),
        provider=_optional_string(source.get("provider")),
        incident_id=UUID(str(incident_id)) if incident_id is not None else None,
        incident_type=_optional_string(source.get("incident_type")),
        environment=_optional_string(source.get("environment")),
        version=_optional_string(source.get("version")),
        metadata=dict(source.get("metadata", {})),
        created_at=datetime.fromisoformat(str(source["created_at"])),
        updated_at=datetime.fromisoformat(str(source["updated_at"])),
    )


def _optional_string(value: object) -> str | None:
    return None if value is None else str(value)
