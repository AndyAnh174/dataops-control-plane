import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import quote
from uuid import UUID, uuid4

from sqlmodel import Session, select

from dataops_control_plane.domain.models import Evidence, Incident, PipelineReport, PipelineRun
from dataops_control_plane.services.data_quality_reports import DATA_QUALITY_REPORT_TYPE
from dataops_control_plane.services.pipeline_logs import (
    LogStoreUnavailable,
    PipelineLogDocument,
    PipelineLogStore,
    redact_log_text,
    redact_log_value,
)

LOG_EVIDENCE_LIMIT = 100
MAX_EVIDENCE_EXCERPT_CHARS = 20_000


@dataclass(frozen=True, slots=True)
class EvidenceCandidate:
    evidence_type: str
    source_uri: str
    excerpt: str
    metadata: dict[str, object]


class EvidenceSource(Protocol):
    def collect(self, run: PipelineRun) -> Sequence[EvidenceCandidate]: ...

    def close(self) -> None: ...


class EvidenceSourceUnavailable(RuntimeError):
    def __init__(
        self,
        source: str,
        message: str,
        *,
        code: str = "SOURCE_UNAVAILABLE",
    ) -> None:
        super().__init__(message)
        self.source = source
        self.code = code


@dataclass(frozen=True, slots=True)
class CollectionWarning:
    source: str
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class EvidenceCollectionResult:
    collected_count: int
    duplicate_count: int
    evidence_count: int
    warnings: tuple[CollectionWarning, ...]


def collect_incident_evidence(
    session: Session,
    incident: Incident,
    run: PipelineRun,
    log_store: PipelineLogStore,
    evidence_sources: Sequence[EvidenceSource] = (),
) -> EvidenceCollectionResult:
    incident.status = "COLLECTING_EVIDENCE"
    incident.updated_at = datetime.now(UTC)
    session.add(incident)

    candidates = [_pipeline_metadata_candidate(run)]
    report_candidate = _data_quality_report_candidate(session, run)
    if report_candidate is not None:
        candidates.append(report_candidate)
    warnings: list[CollectionWarning] = []

    try:
        log_candidate = _pipeline_log_candidate(run, log_store)
    except LogStoreUnavailable:
        log_candidate = None
        warnings.append(
            CollectionWarning(
                source="pipeline_logs",
                code="SOURCE_UNAVAILABLE",
                message="Pipeline log storage is temporarily unavailable",
            )
        )
    if log_candidate is None and not warnings:
        failed_stage = run.failed_stage or "<unknown>"
        warnings.append(
            CollectionWarning(
                source="pipeline_logs",
                code="NO_MATCHING_LOGS",
                message=f"No logs found for failed stage '{failed_stage}'",
            )
        )
    elif log_candidate is not None:
        candidates.append(log_candidate)

    for source in evidence_sources:
        try:
            candidates.extend(source.collect(run))
        except EvidenceSourceUnavailable as exc:
            warnings.append(
                CollectionWarning(
                    source=exc.source,
                    code=exc.code,
                    message=str(exc),
                )
            )

    collected_count = 0
    duplicate_count = 0
    for candidate in candidates:
        sanitized = _sanitize_candidate(candidate)
        checksum = _candidate_checksum(sanitized)
        existing = session.exec(
            select(Evidence).where(
                Evidence.incident_id == incident.id,
                Evidence.evidence_type == sanitized.evidence_type,
                Evidence.source_uri == sanitized.source_uri,
                Evidence.checksum == checksum,
            )
        ).one_or_none()
        if existing is not None:
            duplicate_count += 1
            continue

        evidence_id = uuid4()
        session.add(
            Evidence(
                id=evidence_id,
                citation_id=f"EVD-{evidence_id.hex.upper()}",
                incident_id=incident.id,
                evidence_type=sanitized.evidence_type,
                source_uri=sanitized.source_uri,
                checksum=checksum,
                excerpt=sanitized.excerpt,
                details=sanitized.metadata,
                collected_at=datetime.now(UTC),
            )
        )
        collected_count += 1

    incident.status = "ANALYZING" if log_candidate is not None else "ACTION_REQUIRED"
    incident.updated_at = datetime.now(UTC)
    session.add(incident)
    session.commit()
    session.refresh(incident)

    evidence_count = len(
        session.exec(select(Evidence).where(Evidence.incident_id == incident.id)).all()
    )
    return EvidenceCollectionResult(
        collected_count=collected_count,
        duplicate_count=duplicate_count,
        evidence_count=evidence_count,
        warnings=tuple(warnings),
    )


def list_incident_evidence(session: Session, incident_id: UUID) -> list[Evidence]:
    statement = (
        select(Evidence)
        .where(Evidence.incident_id == incident_id)
        .order_by(Evidence.collected_at, Evidence.citation_id)
    )
    return list(session.exec(statement).all())


def _pipeline_metadata_candidate(run: PipelineRun) -> EvidenceCandidate:
    metadata = {
        "run_id": str(run.id),
        "provider": run.provider,
        "project_ref": run.project_ref,
        "external_run_id": run.external_run_id,
        "attempt": run.attempt,
        "commit_sha": run.commit_sha,
        "branch": run.branch,
        "status": run.status,
        "failed_stage": run.failed_stage,
        "last_event_at": _utc_iso(run.last_event_at),
    }
    return EvidenceCandidate(
        evidence_type="PIPELINE_METADATA",
        source_uri=f"postgresql://pipeline-runs/{run.id}",
        excerpt=json.dumps(metadata, sort_keys=True, separators=(",", ":")),
        metadata=metadata,
    )


def _data_quality_report_candidate(
    session: Session,
    run: PipelineRun,
) -> EvidenceCandidate | None:
    report = session.exec(
        select(PipelineReport)
        .where(
            PipelineReport.pipeline_run_id == run.id,
            PipelineReport.report_type == DATA_QUALITY_REPORT_TYPE,
        )
        .order_by(PipelineReport.received_at.desc())
    ).first()
    if report is None:
        return None

    excerpt = json.dumps(report.payload, sort_keys=True, separators=(",", ":"))
    truncated = False
    if len(excerpt) > MAX_EVIDENCE_EXCERPT_CHARS:
        suffix = "...[TRUNCATED]"
        excerpt = excerpt[: MAX_EVIDENCE_EXCERPT_CHARS - len(suffix)] + suffix
        truncated = True

    return EvidenceCandidate(
        evidence_type="DATA_QUALITY_REPORT",
        source_uri=report.source_uri,
        excerpt=excerpt,
        metadata={
            "report_id": str(report.id),
            "run_id": str(run.id),
            "report_type": report.report_type,
            "checksum": report.checksum,
            "contract": report.payload["contract"],
            "scenario": report.payload["scenario"],
            "success": report.payload["success"],
            "summary": report.payload["summary"],
            "redaction_count": report.redaction_count,
            "truncated": truncated,
        },
    )


def _pipeline_log_candidate(
    run: PipelineRun,
    log_store: PipelineLogStore,
) -> EvidenceCandidate | None:
    documents = log_store.search(
        run.id,
        query=None,
        stage=run.failed_stage,
        level=None,
        limit=LOG_EVIDENCE_LIMIT,
    )
    if not documents:
        return None

    excerpt, truncated = _format_log_excerpt(documents)
    failed_stage = run.failed_stage or ""
    return EvidenceCandidate(
        evidence_type="LOG_EXCERPT",
        source_uri=(
            f"elasticsearch://pipeline-logs/runs/{run.id}"
            f"?stage={quote(failed_stage, safe='')}&limit={LOG_EVIDENCE_LIMIT}"
        ),
        excerpt=excerpt,
        metadata={
            "run_id": str(run.id),
            "stage": run.failed_stage,
            "log_count": len(documents),
            "levels": sorted({document.level for document in documents}),
            "source_redaction_count": sum(document.redaction_count for document in documents),
            "collector_redaction_count": 0,
            "truncated": truncated,
        },
    )


def _format_log_excerpt(documents: list[PipelineLogDocument]) -> tuple[str, bool]:
    lines: list[str] = []
    for document in documents:
        lines.append(
            f"{_utc_iso(document.occurred_at)} [{document.level}] "
            f"{document.job_name}/{document.stage} {document.stream}#{document.sequence} "
            f"{document.message}"
        )
        if document.stack_trace:
            lines.append(document.stack_trace)

    excerpt = "\n".join(lines)
    if len(excerpt) <= MAX_EVIDENCE_EXCERPT_CHARS:
        return excerpt, False
    suffix = "\n...[TRUNCATED]"
    return excerpt[: MAX_EVIDENCE_EXCERPT_CHARS - len(suffix)] + suffix, True


def _sanitize_candidate(candidate: EvidenceCandidate) -> EvidenceCandidate:
    excerpt, excerpt_redactions = redact_log_text(candidate.excerpt)
    metadata_value, metadata_redactions = redact_log_value(candidate.metadata)
    metadata = dict(metadata_value)
    collector_redactions = excerpt_redactions + metadata_redactions
    if collector_redactions > 0 or candidate.evidence_type == "LOG_EXCERPT":
        metadata["collector_redaction_count"] = collector_redactions
    return EvidenceCandidate(
        evidence_type=candidate.evidence_type,
        source_uri=candidate.source_uri,
        excerpt=excerpt,
        metadata=metadata,
    )


def _candidate_checksum(candidate: EvidenceCandidate) -> str:
    canonical = json.dumps(
        {
            "evidence_type": candidate.evidence_type,
            "source_uri": candidate.source_uri,
            "excerpt": candidate.excerpt,
            "metadata": candidate.metadata,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return value.isoformat().replace("+00:00", "Z")
