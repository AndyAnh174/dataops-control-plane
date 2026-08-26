from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, status

from dataops_control_plane.api.dependencies import SessionDep
from dataops_control_plane.api.schemas import PipelineRunRead
from dataops_control_plane.domain.models import PipelineRun

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


@router.get("/{run_id}")
def get_pipeline_run(
    run_id: Annotated[UUID, Path(description="Internal pipeline run ID")],
    session: SessionDep,
) -> PipelineRunRead:
    run = session.get(PipelineRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline run not found")
    return PipelineRunRead.model_validate(run)
