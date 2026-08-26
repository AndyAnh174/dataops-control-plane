from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status

from dataops_control_plane.api.dependencies import SessionDep, require_agent_token
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


@router.post(
    "/{run_id}/reports/data-quality",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_agent_token)],
)
def post_data_quality_report(
    run_id: Annotated[UUID, Path(description="Internal pipeline run ID")],
    report: DataQualityReportCreate,
    session: SessionDep,
) -> DataQualityReportReceipt:
    run = session.get(PipelineRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline run not found")
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
