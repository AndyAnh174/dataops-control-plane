from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, status
from sqlmodel import select

from dataops_control_plane.api.dependencies import (
    AgentPrincipalDep,
    OperatorPrincipalDep,
    RecoveryExecutorDep,
    SessionDep,
    require_agent_run_access,
    require_operator_run_access,
)
from dataops_control_plane.api.schemas import (
    RecoveryApprovalRequest,
    RecoveryAttemptRead,
    RecoveryAttemptReceipt,
    RecoveryAuditEventRead,
    RecoveryAuditListResponse,
    RecoveryPlanRead,
    RecoveryPlanReceipt,
    RecoveryRejectionRequest,
    RecoveryVerificationCreate,
)
from dataops_control_plane.domain.models import (
    Incident,
    PipelineRun,
    RecoveryAttempt,
    RecoveryAuditEvent,
    RecoveryPlan,
)
from dataops_control_plane.services.recovery_execution import (
    RecoveryDispatchUnavailable,
    RecoveryExecutionUnavailable,
    RecoveryVerificationConflict,
    execute_recovery_plan,
    verify_recovery_attempt,
)
from dataops_control_plane.services.recovery_policy import (
    RecoveryPlanAlreadyDecided,
    RecoveryPlanUnavailable,
    approve_recovery_plan,
    create_recovery_plan,
    reject_recovery_plan,
)

router = APIRouter(
    prefix="/api/v1/incidents",
    tags=["recovery"],
)


@router.post(
    "/{incident_id}/recovery-plans",
    status_code=status.HTTP_201_CREATED,
)
def create_plan(
    incident_id: Annotated[UUID, Path(description="Incident ID")],
    principal: OperatorPrincipalDep,
    session: SessionDep,
) -> RecoveryPlanReceipt:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    _require_incident_access(session, principal, incident)
    try:
        result = create_recovery_plan(session, incident)
    except RecoveryPlanUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return RecoveryPlanReceipt(**_plan_read(result.plan).model_dump(), duplicate=result.duplicate)


@router.post("/{incident_id}/recovery-plans/{plan_id}/approve")
def approve_plan(
    request: RecoveryApprovalRequest,
    incident_id: Annotated[UUID, Path(description="Incident ID")],
    plan_id: Annotated[UUID, Path(description="Recovery plan ID")],
    principal: OperatorPrincipalDep,
    session: SessionDep,
) -> RecoveryPlanRead:
    plan = _get_plan(session, incident_id, plan_id)
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    _require_incident_access(session, principal, incident)
    try:
        approved = approve_recovery_plan(session, plan, actor=request.actor)
    except (RecoveryPlanAlreadyDecided, RecoveryPlanUnavailable) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _plan_read(approved)


@router.post("/{incident_id}/recovery-plans/{plan_id}/reject")
def reject_plan(
    request: RecoveryRejectionRequest,
    incident_id: Annotated[UUID, Path(description="Incident ID")],
    plan_id: Annotated[UUID, Path(description="Recovery plan ID")],
    principal: OperatorPrincipalDep,
    session: SessionDep,
) -> RecoveryPlanRead:
    plan = _get_plan(session, incident_id, plan_id)
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    _require_incident_access(session, principal, incident)
    try:
        rejected = reject_recovery_plan(
            session,
            plan,
            actor=request.actor,
            reason=request.reason,
        )
    except RecoveryPlanAlreadyDecided as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _plan_read(rejected)


@router.post(
    "/{incident_id}/recovery-plans/{plan_id}/execute",
    status_code=status.HTTP_202_ACCEPTED,
)
def execute_plan(
    incident_id: Annotated[UUID, Path(description="Incident ID")],
    plan_id: Annotated[UUID, Path(description="Recovery plan ID")],
    principal: OperatorPrincipalDep,
    session: SessionDep,
    executor: RecoveryExecutorDep,
) -> RecoveryAttemptReceipt:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    _require_incident_access(session, principal, incident)
    plan = _get_plan(session, incident_id, plan_id)
    try:
        result = execute_recovery_plan(session, incident, plan, executor)
    except RecoveryDispatchUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except RecoveryExecutionUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return RecoveryAttemptReceipt(
        **_attempt_read(result.attempt).model_dump(),
        duplicate=result.duplicate,
    )


@router.get("/{incident_id}/recovery-audit")
def get_recovery_audit(
    incident_id: Annotated[UUID, Path(description="Incident ID")],
    principal: OperatorPrincipalDep,
    session: SessionDep,
) -> RecoveryAuditListResponse:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    _require_incident_access(session, principal, incident)
    events = session.exec(
        select(RecoveryAuditEvent)
        .where(RecoveryAuditEvent.incident_id == incident_id)
        .order_by(RecoveryAuditEvent.created_at.asc())
    ).all()
    return RecoveryAuditListResponse(
        items=[RecoveryAuditEventRead.model_validate(item, from_attributes=True) for item in events]
    )


@router.post("/{incident_id}/recovery-attempts/{attempt_id}/verification")
def verify_attempt(
    request: RecoveryVerificationCreate,
    incident_id: Annotated[UUID, Path(description="Incident ID")],
    attempt_id: Annotated[UUID, Path(description="Recovery attempt ID")],
    principal: AgentPrincipalDep,
    session: SessionDep,
) -> RecoveryAttemptReceipt:
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    pipeline_run = session.get(PipelineRun, incident.pipeline_run_id)
    if pipeline_run is None:
        raise RuntimeError(f"Incident {incident.id} references an unknown pipeline run")
    require_agent_run_access(principal, pipeline_run, scope="verification:write")
    attempt = session.get(RecoveryAttempt, attempt_id)
    if attempt is None or attempt.incident_id != incident_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recovery attempt not found",
        )
    try:
        result = verify_recovery_attempt(
            session,
            incident,
            attempt,
            idempotency_key=request.idempotency_key,
            verification_status=request.status.value,
            external_reference=request.external_reference,
            details=dict(request.details),
        )
    except RecoveryVerificationConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return RecoveryAttemptReceipt(
        **_attempt_read(result.attempt).model_dump(),
        duplicate=result.duplicate,
    )


def _get_plan(session, incident_id: UUID, plan_id: UUID) -> RecoveryPlan:
    plan = session.get(RecoveryPlan, plan_id)
    if plan is None or plan.incident_id != incident_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recovery plan not found",
        )
    return plan


def _require_incident_access(session, principal, incident: Incident) -> None:
    pipeline_run = session.get(PipelineRun, incident.pipeline_run_id)
    if pipeline_run is None:
        raise RuntimeError(f"Incident {incident.id} references an unknown pipeline run")
    require_operator_run_access(principal, session, pipeline_run)


def _plan_read(plan: RecoveryPlan) -> RecoveryPlanRead:
    return RecoveryPlanRead.model_validate(plan, from_attributes=True)


def _attempt_read(attempt) -> RecoveryAttemptRead:
    return RecoveryAttemptRead.model_validate(attempt, from_attributes=True)
