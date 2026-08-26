from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from dataops_control_plane.api.dependencies import LogStoreDep, SessionDep, require_agent_token
from dataops_control_plane.api.schemas import (
    PipelineLogBatchCreate,
    PipelineLogRead,
    PipelineLogReceipt,
    PipelineLogSearchResponse,
)
from dataops_control_plane.domain.models import PipelineRun
from dataops_control_plane.services.pipeline_logs import (
    LogStoreUnavailable,
    build_log_documents,
)

router = APIRouter(
    prefix="/api/v1/runs",
    tags=["logs"],
    dependencies=[Depends(require_agent_token)],
)


@router.post("/{run_id}/logs", status_code=status.HTTP_202_ACCEPTED)
def ingest_pipeline_logs(
    run_id: Annotated[UUID, Path(description="Internal pipeline run ID")],
    batch: PipelineLogBatchCreate,
    session: SessionDep,
    log_store: LogStoreDep,
) -> PipelineLogReceipt:
    run = session.get(PipelineRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline run not found")

    documents, redaction_count = build_log_documents(run, batch.entries)
    try:
        result = log_store.append(documents)
    except LogStoreUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Log storage is temporarily unavailable",
        ) from exc

    return PipelineLogReceipt(
        run_id=run_id,
        accepted_count=result.accepted_count,
        duplicate_count=result.duplicate_count,
        redaction_count=redaction_count,
    )


@router.get("/{run_id}/logs", response_model_exclude_none=True)
def search_pipeline_logs(
    run_id: Annotated[UUID, Path(description="Internal pipeline run ID")],
    session: SessionDep,
    log_store: LogStoreDep,
    query: Annotated[str | None, Query(min_length=1, max_length=500)] = None,
    stage: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    level: Annotated[
        str | None,
        Query(pattern=r"^(TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL)$"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> PipelineLogSearchResponse:
    if session.get(PipelineRun, run_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline run not found")

    try:
        documents = log_store.search(
            run_id,
            query=query,
            stage=stage,
            level=level,
            limit=limit,
        )
    except LogStoreUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Log storage is temporarily unavailable",
        ) from exc

    return PipelineLogSearchResponse(
        items=[
            PipelineLogRead(
                occurred_at=document.occurred_at,
                job_name=document.job_name,
                stage=document.stage,
                level=document.level,
                stream=document.stream,
                sequence=document.sequence,
                message=document.message,
                stack_trace=document.stack_trace,
                tags=list(document.tags),
                metadata=document.metadata,
                redaction_count=document.redaction_count,
            )
            for document in documents
        ]
    )
