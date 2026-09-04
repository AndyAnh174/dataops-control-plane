from fastapi import APIRouter, HTTPException, status

from dataops_control_plane.api.dependencies import (
    AgentPrincipalDep,
    SessionDep,
    require_agent_scope,
)
from dataops_control_plane.api.schemas import PipelineEventCreate, PipelineEventReceipt
from dataops_control_plane.services.pipeline_events import InvalidPipelineTransition
from dataops_control_plane.services.pipeline_events import ingest_pipeline_event as ingest_event

router = APIRouter(
    prefix="/api/v1/events",
    tags=["events"],
)


@router.post("/pipeline", status_code=status.HTTP_202_ACCEPTED)
def ingest_pipeline_event(
    event: PipelineEventCreate,
    principal: AgentPrincipalDep,
    session: SessionDep,
) -> PipelineEventReceipt:
    require_agent_scope(principal, "runs:write")
    if principal.authentication_type == "project-token" and (
        principal.project_ref != event.project_ref or principal.provider != event.provider
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token does not belong to this pipeline project",
        )
    try:
        run, duplicate = ingest_event(session, event)
    except InvalidPipelineTransition as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return PipelineEventReceipt(
        event_id=event.event_id,
        run_id=run.id,
        duplicate=duplicate,
        run_status=run.status,
    )
