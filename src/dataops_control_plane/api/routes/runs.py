from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, status

from dataops_control_plane.api.dependencies import (
    AgentPrincipalDep,
    SessionDep,
    WebReadUserDep,
    require_agent_run_access,
)
from dataops_control_plane.api.schemas import (
    DataQualityReportCreate,
    DataQualityReportReceipt,
    PipelineRunRead,
)
from dataops_control_plane.domain.models import PipelineRun
from dataops_control_plane.services.data_quality_reports import (
    DataQualityReportTooLarge,
    ingest_data_quality_report,
)
from dataops_control_plane.services.web_projects import ProjectNotFound, require_pipeline_run_access

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


@router.get("/{run_id}")
def get_pipeline_run(
    run_id: Annotated[UUID, Path(description="Internal pipeline run ID")],
    user: WebReadUserDep,
    session: SessionDep,
) -> PipelineRunRead:
    run = session.get(PipelineRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline run not found")
    if user is not None:
        try:
            require_pipeline_run_access(session, run=run, user_id=user.id)
        except ProjectNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PipelineRunRead.model_validate(run)


@router.post(
    "/{run_id}/reports/data-quality",
    status_code=status.HTTP_202_ACCEPTED,
)
def post_data_quality_report(
    run_id: Annotated[UUID, Path(description="Internal pipeline run ID")],
    report: DataQualityReportCreate,
    principal: AgentPrincipalDep,
    session: SessionDep,
) -> DataQualityReportReceipt:
    run = session.get(PipelineRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline run not found")
    require_agent_run_access(principal, run, scope="reports:write")
    try:
        result = ingest_data_quality_report(session, run, report)
    except DataQualityReportTooLarge as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(exc),
        ) from exc
    return DataQualityReportReceipt(
        report_id=result.report.id,
        run_id=run.id,
        checksum=result.report.checksum,
        duplicate=result.duplicate,
    )
