from datetime import UTC, datetime
from typing import cast

from sqlmodel import Session, select

from dataops_control_plane.api.schemas import PipelineEventCreate
from dataops_control_plane.domain.models import PipelineRun, ProcessedEvent


class InvalidPipelineTransition(ValueError):
    def __init__(self, current_status: str, next_status: str) -> None:
        super().__init__(f"Invalid pipeline status transition: {current_status} -> {next_status}")


def ensure_valid_transition(current_status: str, next_status: str) -> None:
    allowed_transitions = {
        "RUNNING": {"RUNNING", "SUCCESS", "FAILED", "CANCELED"},
        "SUCCESS": {"SUCCESS"},
        "FAILED": {"FAILED"},
        "CANCELED": {"CANCELED"},
    }
    if next_status not in allowed_transitions[current_status]:
        raise InvalidPipelineTransition(current_status, next_status)


def create_pipeline_run(session: Session, event: PipelineEventCreate) -> PipelineRun:
    run = PipelineRun(
        provider=event.provider,
        project_ref=event.project_ref,
        external_run_id=event.external_run_id,
        attempt=event.attempt,
        commit_sha=event.commit_sha,
        branch=event.branch,
        status=event.status.value,
        failed_stage=event.failed_stage,
        last_event_at=event.occurred_at,
    )
    session.add(run)
    session.add(
        ProcessedEvent(
            event_id=event.event_id,
            pipeline_run_id=run.id,
            received_at=datetime.now(UTC),
        )
    )
    session.commit()
    session.refresh(run)
    return run


def ingest_pipeline_event(
    session: Session,
    event: PipelineEventCreate,
) -> tuple[PipelineRun, bool]:
    processed_event = session.get(ProcessedEvent, event.event_id)
    if processed_event is not None:
        existing_run = session.get(PipelineRun, processed_event.pipeline_run_id)
        return cast(PipelineRun, existing_run), True

    statement = select(PipelineRun).where(
        PipelineRun.provider == event.provider,
        PipelineRun.project_ref == event.project_ref,
        PipelineRun.external_run_id == event.external_run_id,
        PipelineRun.attempt == event.attempt,
    )
    run = session.exec(statement).one_or_none()
    if run is None:
        return create_pipeline_run(session, event), False

    ensure_valid_transition(run.status, event.status.value)
    run.status = event.status.value
    run.failed_stage = event.failed_stage
    run.last_event_at = event.occurred_at
    session.add(run)
    session.add(
        ProcessedEvent(
            event_id=event.event_id,
            pipeline_run_id=run.id,
            received_at=datetime.now(UTC),
        )
    )
    session.commit()
    session.refresh(run)
    return run, False
