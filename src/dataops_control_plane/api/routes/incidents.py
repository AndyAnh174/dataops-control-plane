from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlmodel import Session, select

from dataops_control_plane.api.dependencies import (
    EvidenceSourcesDep,
    HybridRetrieverDep,
    LogStoreDep,
    RCAAgentDep,
    SessionDep,
    require_agent_token,
)
from dataops_control_plane.api.schemas import (
    EvidenceCollectionReceipt,
    EvidenceCollectionWarning,
    EvidenceListResponse,
    EvidenceRead,
    IncidentListResponse,
    IncidentRead,
    KnowledgeDocumentReceipt,
    PipelineRunRead,
    RCAAnalysisReceipt,
    RCAEvidenceClaim,
    RCARecommendedAction,
    RCAReportRead,
)
from dataops_control_plane.domain.models import Evidence, Incident, PipelineRun, RCAReport
from dataops_control_plane.services.evidence import (
    collect_incident_evidence,
    list_incident_evidence,
)
from dataops_control_plane.services.rca_agent import (
    InsufficientEvidence,
    LLMResponseInvalid,
    LLMUnavailable,
    RCAValidationError,
)
from dataops_control_plane.services.retrieval import (
    EmbeddingUnavailable,
    KnowledgeStoreUnavailable,
)

router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"])


def build_incident_read(
    session: Session,
    incident: Incident,
) -> IncidentRead:
    pipeline_run = session.get(PipelineRun, incident.pipeline_run_id)
    if pipeline_run is None:
        raise RuntimeError(f"Incident {incident.id} references an unknown pipeline run")
    return IncidentRead(
        id=incident.id,
        status=incident.status,
        trigger_event_id=incident.trigger_event_id,
        created_at=incident.created_at,
        updated_at=incident.updated_at,
        pipeline_run=PipelineRunRead.model_validate(pipeline_run),
    )


@router.get("")
def list_incidents(session: SessionDep) -> IncidentListResponse:
    statement = select(Incident).order_by(Incident.created_at.desc())
    incidents = session.exec(statement).all()
    return IncidentListResponse(
        items=[build_incident_read(session, incident) for incident in incidents]
    )


@router.get("/{incident_id}")
def get_incident(
    incident_id: Annotated[UUID, Path(description="Incident ID")],
    session: SessionDep,
) -> IncidentRead:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return build_incident_read(session, incident)


def build_evidence_read(evidence: Evidence) -> EvidenceRead:
    return EvidenceRead(
        id=evidence.id,
        incident_id=evidence.incident_id,
        citation_id=evidence.citation_id,
        evidence_type=evidence.evidence_type,
        source_uri=evidence.source_uri,
        checksum=evidence.checksum,
        excerpt=evidence.excerpt,
        metadata=evidence.details,
        collected_at=evidence.collected_at,
    )


@router.post(
    "/{incident_id}/collect-evidence",
    dependencies=[Depends(require_agent_token)],
)
def collect_evidence(
    incident_id: Annotated[UUID, Path(description="Incident ID")],
    session: SessionDep,
    log_store: LogStoreDep,
    evidence_sources: EvidenceSourcesDep,
) -> EvidenceCollectionReceipt:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    pipeline_run = session.get(PipelineRun, incident.pipeline_run_id)
    if pipeline_run is None:
        raise RuntimeError(f"Incident {incident.id} references an unknown pipeline run")

    result = collect_incident_evidence(
        session,
        incident,
        pipeline_run,
        log_store,
        evidence_sources,
    )
    return EvidenceCollectionReceipt(
        incident_id=incident.id,
        incident_status=incident.status,
        collected_count=result.collected_count,
        duplicate_count=result.duplicate_count,
        evidence_count=result.evidence_count,
        warnings=[
            EvidenceCollectionWarning(
                source=warning.source,
                code=warning.code,
                message=warning.message,
            )
            for warning in result.warnings
        ],
    )


@router.get(
    "/{incident_id}/evidence",
    dependencies=[Depends(require_agent_token)],
)
def get_evidence(
    incident_id: Annotated[UUID, Path(description="Incident ID")],
    session: SessionDep,
) -> EvidenceListResponse:
    if session.get(Incident, incident_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return EvidenceListResponse(
        items=[
            build_evidence_read(evidence)
            for evidence in list_incident_evidence(session, incident_id)
        ]
    )


@router.post(
    "/{incident_id}/index-knowledge",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_agent_token)],
)
def index_incident_knowledge(
    incident_id: Annotated[UUID, Path(description="Incident ID")],
    session: SessionDep,
    retriever: HybridRetrieverDep,
) -> KnowledgeDocumentReceipt:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    try:
        result = retriever.index_incident(session, incident)
    except (EmbeddingUnavailable, KnowledgeStoreUnavailable) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return KnowledgeDocumentReceipt(
        document_id=result.document.document_id,
        document_type=result.document.document_type,
        checksum=result.document.checksum,
        result=result.result,
        embedding_model=result.document.embedding_model,
        redaction_count=result.redaction_count,
    )


@router.post(
    "/{incident_id}/analyze",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_agent_token)],
)
def analyze_incident(
    incident_id: Annotated[UUID, Path(description="Incident ID")],
    session: SessionDep,
    rca_agent: RCAAgentDep,
) -> RCAAnalysisReceipt:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    try:
        result = rca_agent.analyze(session, incident)
    except InsufficientEvidence as exc:
        incident.status = "ACTION_REQUIRED"
        session.add(incident)
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": str(exc),
                "missing_information": list(exc.missing_information),
            },
        ) from exc
    except (EmbeddingUnavailable, KnowledgeStoreUnavailable, LLMUnavailable) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except (LLMResponseInvalid, RCAValidationError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return RCAAnalysisReceipt(
        **_report_read(result.report).model_dump(),
        duplicate=result.duplicate,
    )


@router.get(
    "/{incident_id}/rca",
    dependencies=[Depends(require_agent_token)],
)
def get_incident_rca(
    incident_id: Annotated[UUID, Path(description="Incident ID")],
    session: SessionDep,
) -> RCAReportRead:
    if session.get(Incident, incident_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    report = session.exec(
        select(RCAReport)
        .where(RCAReport.incident_id == incident_id)
        .order_by(RCAReport.created_at.desc())
    ).first()
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="RCA report not found")
    return _report_read(report)


def _report_read(report: RCAReport) -> RCAReportRead:
    return RCAReportRead(
        id=report.id,
        incident_id=report.incident_id,
        analysis_status=report.analysis_status,
        incident_type=report.incident_type,
        root_cause=report.root_cause,
        confidence=report.confidence,
        evidence=[RCAEvidenceClaim.model_validate(item) for item in report.evidence_claims],
        knowledge_document_ids=report.knowledge_document_ids,
        recommended_action=RCARecommendedAction.model_validate(report.recommended_action),
        missing_information=report.missing_information,
        model_name=report.model_name,
        embedding_model=report.embedding_model,
        prompt_version=report.prompt_version,
        input_checksum=report.input_checksum,
        llm_calls=report.llm_calls,
        prompt_tokens=report.prompt_tokens,
        completion_tokens=report.completion_tokens,
        duration_ms=report.duration_ms,
        graph_trace=report.graph_trace,
        created_at=report.created_at,
    )
