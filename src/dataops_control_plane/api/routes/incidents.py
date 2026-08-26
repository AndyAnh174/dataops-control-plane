from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlmodel import Session, select

from dataops_control_plane.api.dependencies import (
    EvidenceSourcesDep,
    LogStoreDep,
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
    PipelineRunRead,
)
from dataops_control_plane.domain.models import Evidence, Incident, PipelineRun
from dataops_control_plane.services.evidence import (
    collect_incident_evidence,
    list_incident_evidence,
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
