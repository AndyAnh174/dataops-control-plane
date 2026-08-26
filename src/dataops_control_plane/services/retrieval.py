import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlmodel import Session, select

from dataops_control_plane.domain.models import Evidence, Incident, PipelineRun
from dataops_control_plane.services.pipeline_logs import redact_log_text, redact_log_value

MAX_KNOWLEDGE_CONTENT_CHARS = 50_000
MAX_INCIDENT_SUMMARY_CHARS = 20_000
MAX_EMBEDDING_INPUT_CHARS = 1_800
DEFAULT_RANK_CONSTANT = 60


class EmbeddingUnavailable(RuntimeError):
    pass


class KnowledgeStoreUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class KnowledgeFilter:
    project_ref: str | None = None
    document_types: tuple[str, ...] = ()
    provider: str | None = None
    incident_type: str | None = None
    environment: str | None = None
    created_after: datetime | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    document_id: str
    checksum: str
    document_type: str
    title: str
    content: str
    source_uri: str
    embedding: list[float]
    embedding_model: str
    project_ref: str | None
    provider: str | None
    incident_id: UUID | None
    incident_type: str | None
    environment: str | None
    version: str | None
    metadata: dict[str, object]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class KnowledgeWriteResult:
    result: str


@dataclass(frozen=True, slots=True)
class KnowledgeSearchCandidate:
    document: KnowledgeDocument
    score: float


@dataclass(frozen=True, slots=True)
class HybridSearchItem:
    document: KnowledgeDocument
    rrf_score: float
    matched_by: tuple[str, ...]
    keyword_rank: int | None = None
    keyword_score: float | None = None
    vector_rank: int | None = None
    vector_score: float | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeIndexResult:
    document: KnowledgeDocument
    result: str
    redaction_count: int


@dataclass(frozen=True, slots=True)
class HybridSearchResult:
    query: str
    items: tuple[HybridSearchItem, ...]
    candidate_limit: int
    redaction_count: int


class EmbeddingProvider(Protocol):
    model_name: str
    dimensions: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    def close(self) -> None: ...


class KnowledgeStore(Protocol):
    def upsert(self, document: KnowledgeDocument) -> KnowledgeWriteResult: ...

    def keyword_search(
        self,
        query: str,
        *,
        filters: KnowledgeFilter,
        limit: int,
    ) -> list[KnowledgeSearchCandidate]: ...

    def vector_search(
        self,
        embedding: Sequence[float],
        *,
        filters: KnowledgeFilter,
        limit: int,
    ) -> list[KnowledgeSearchCandidate]: ...

    def close(self) -> None: ...


def reciprocal_rank_fusion(
    keyword_results: Sequence[KnowledgeSearchCandidate],
    vector_results: Sequence[KnowledgeSearchCandidate],
    *,
    rank_constant: int = DEFAULT_RANK_CONSTANT,
) -> list[HybridSearchItem]:
    if rank_constant < 1:
        raise ValueError("rank_constant must be positive")

    fused: dict[str, dict[str, object]] = {}
    for branch, results in (("keyword", keyword_results), ("vector", vector_results)):
        for rank, candidate in enumerate(results, start=1):
            entry = fused.setdefault(
                candidate.document.document_id,
                {"document": candidate.document, "rrf_score": 0.0},
            )
            entry["rrf_score"] = float(entry["rrf_score"]) + 1 / (rank_constant + rank)
            entry[f"{branch}_rank"] = rank
            entry[f"{branch}_score"] = candidate.score

    items = [
        HybridSearchItem(
            document=entry["document"],
            rrf_score=float(entry["rrf_score"]),
            matched_by=tuple(
                branch for branch in ("keyword", "vector") if f"{branch}_rank" in entry
            ),
            keyword_rank=_optional_int(entry.get("keyword_rank")),
            keyword_score=_optional_float(entry.get("keyword_score")),
            vector_rank=_optional_int(entry.get("vector_rank")),
            vector_score=_optional_float(entry.get("vector_score")),
        )
        for entry in fused.values()
    ]
    return sorted(items, key=lambda item: (-item.rrf_score, item.document.document_id))


class HybridRetriever:
    def __init__(
        self,
        store: KnowledgeStore,
        embedder: EmbeddingProvider,
        *,
        rank_constant: int = DEFAULT_RANK_CONSTANT,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self.rank_constant = rank_constant

    @property
    def embedding_model(self) -> str:
        return self._embedder.model_name

    def index_document(
        self,
        *,
        document_type: str,
        title: str,
        content: str,
        source_uri: str,
        project_ref: str | None = None,
        provider: str | None = None,
        incident_id: UUID | None = None,
        incident_type: str | None = None,
        environment: str | None = None,
        version: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> KnowledgeIndexResult:
        safe_title, title_redactions = redact_log_text(title)
        safe_content, content_redactions = redact_log_text(content[:MAX_KNOWLEDGE_CONTENT_CHARS])
        safe_source_uri, source_uri_redactions = redact_log_text(source_uri)
        safe_metadata_value, metadata_redactions = redact_log_value(dict(metadata or {}))
        safe_metadata = dict(safe_metadata_value)
        redaction_count = (
            title_redactions + content_redactions + source_uri_redactions + metadata_redactions
        )

        embedding_input = _bounded_embedding_input(safe_title, safe_content)
        embedding = self._embedder.embed([embedding_input])[0]
        now = datetime.now(UTC)
        identity = f"{document_type}\0{safe_source_uri}"
        document_id = hashlib.sha256(identity.encode()).hexdigest()
        checksum = _document_checksum(
            document_type=document_type,
            title=safe_title,
            content=safe_content,
            source_uri=safe_source_uri,
            project_ref=project_ref,
            provider=provider,
            incident_id=incident_id,
            incident_type=incident_type,
            environment=environment,
            version=version,
            metadata=safe_metadata,
        )
        document = KnowledgeDocument(
            document_id=document_id,
            checksum=checksum,
            document_type=document_type,
            title=safe_title,
            content=safe_content,
            source_uri=safe_source_uri,
            embedding=embedding,
            embedding_model=self._embedder.model_name,
            project_ref=project_ref,
            provider=provider,
            incident_id=incident_id,
            incident_type=incident_type,
            environment=environment,
            version=version,
            metadata=safe_metadata,
            created_at=now,
            updated_at=now,
        )
        write_result = self._store.upsert(document)
        return KnowledgeIndexResult(
            document=document,
            result=write_result.result,
            redaction_count=redaction_count,
        )

    def index_incident(self, session: Session, incident: Incident) -> KnowledgeIndexResult:
        run = session.get(PipelineRun, incident.pipeline_run_id)
        if run is None:
            raise RuntimeError(f"Incident {incident.id} references an unknown pipeline run")
        evidence = list(
            session.exec(
                select(Evidence)
                .where(Evidence.incident_id == incident.id)
                .order_by(Evidence.collected_at, Evidence.citation_id)
            ).all()
        )
        content = _incident_summary_content(incident, run, evidence)
        return self.index_document(
            document_type="INCIDENT_SUMMARY",
            title=f"{run.project_ref}: {run.failed_stage or 'pipeline'} failure",
            content=content,
            source_uri=f"dataops://incidents/{incident.id}",
            project_ref=run.project_ref,
            provider=run.provider,
            incident_id=incident.id,
            incident_type=run.failed_stage or "pipeline",
            environment=_optional_metadata_string(evidence, "environment"),
            version=run.commit_sha,
            metadata={
                "run_id": str(run.id),
                "external_run_id": run.external_run_id,
                "attempt": run.attempt,
                "branch": run.branch,
                "status": run.status,
                "evidence_citations": [item.citation_id for item in evidence],
            },
        )

    def search(
        self,
        query: str,
        *,
        top_k: int,
        filters: KnowledgeFilter,
    ) -> HybridSearchResult:
        safe_query, redaction_count = redact_log_text(query)
        embedding = self._embedder.embed([safe_query])[0]
        candidate_limit = min(max(top_k * 3, 10), 100)
        keyword_results = self._store.keyword_search(
            safe_query,
            filters=filters,
            limit=candidate_limit,
        )
        vector_results = self._store.vector_search(
            embedding,
            filters=filters,
            limit=candidate_limit,
        )
        fused = reciprocal_rank_fusion(
            keyword_results,
            vector_results,
            rank_constant=self.rank_constant,
        )
        return HybridSearchResult(
            query=safe_query,
            items=tuple(fused[:top_k]),
            candidate_limit=candidate_limit,
            redaction_count=redaction_count,
        )

    def close(self) -> None:
        self._store.close()
        self._embedder.close()


def _incident_summary_content(
    incident: Incident,
    run: PipelineRun,
    evidence: Sequence[Evidence],
) -> str:
    header = [
        f"Incident: {incident.id}",
        f"Status: {incident.status}",
        f"Project: {run.project_ref}",
        f"Provider: {run.provider}",
        f"Pipeline run: {run.external_run_id} attempt {run.attempt}",
        f"Commit: {run.commit_sha}",
        f"Branch: {run.branch}",
        f"Failed stage: {run.failed_stage or '<unknown>'}",
    ]
    evidence_sections = [
        f"\n[{item.citation_id}] {item.evidence_type}\nSource: {item.source_uri}\n{item.excerpt}"
        for item in evidence
    ]
    value = "\n".join(header + evidence_sections)
    if len(value) <= MAX_INCIDENT_SUMMARY_CHARS:
        return value
    suffix = "\n...[TRUNCATED]"
    return value[: MAX_INCIDENT_SUMMARY_CHARS - len(suffix)] + suffix


def _optional_metadata_string(evidence: Sequence[Evidence], key: str) -> str | None:
    for item in evidence:
        value = item.details.get(key)
        if value is not None:
            return str(value)
    return None


def _document_checksum(**values: object) -> str:
    canonical = json.dumps(values, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _bounded_embedding_input(title: str, content: str) -> str:
    prefix = f"{title}\n"
    content_budget = MAX_EMBEDDING_INPUT_CHARS - len(prefix)
    if len(content) <= content_budget:
        return prefix + content

    marker = "\n...[MIDDLE OMITTED FOR EMBEDDING]...\n"
    excerpt_budget = content_budget - len(marker)
    head_chars = excerpt_budget // 2
    tail_chars = excerpt_budget - head_chars
    return prefix + content[:head_chars] + marker + content[-tail_chars:]


def _optional_int(value: object) -> int | None:
    return None if value is None else int(value)


def _optional_float(value: object) -> float | None:
    return None if value is None else float(value)
