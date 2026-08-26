from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, status
from sqlmodel import Session, select

from dataops_control_plane.api.dependencies import SessionDep
from dataops_control_plane.api.schemas import (
    IncidentListResponse,
    IncidentRead,
    PipelineRunRead,
)
from dataops_control_plane.domain.models import Incident, PipelineRun

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
