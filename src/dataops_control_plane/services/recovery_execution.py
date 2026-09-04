from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol
from uuid import UUID

from sqlmodel import Session, select

from dataops_control_plane.domain.models import (
    Incident,
    PipelineRun,
    RecoveryAttempt,
    RecoveryPlan,
)
from dataops_control_plane.services.recovery_audit import record_recovery_event


class RecoveryExecutionUnavailable(RuntimeError):
    pass


class RecoveryDispatchUnavailable(RecoveryExecutionUnavailable):
    pass


class RecoveryExecutorError(RuntimeError):
    pass


@dataclass(frozen=True)
class RecoveryRequest:
    incident_id: UUID
    attempt_id: UUID
    project_ref: str
    branch: str
    action_type: str
    parameters: dict[str, object]
    idempotency_key: str


@dataclass(frozen=True)
class RecoveryDispatch:
    external_reference: str
    details: dict[str, object]


class RecoveryExecutor(Protocol):
    provider: str
    capabilities: frozenset[str]

    def execute(self, request: RecoveryRequest) -> RecoveryDispatch: ...

    def close(self) -> None: ...


class DisabledRecoveryExecutor:
    provider = "disabled"
    capabilities: frozenset[str] = frozenset()

    def execute(self, request: RecoveryRequest) -> RecoveryDispatch:
        raise RecoveryExecutorError("Recovery execution is not configured")

    def close(self) -> None:
        pass


@dataclass(frozen=True)
class RecoveryAttemptResult:
    attempt: RecoveryAttempt
    duplicate: bool


class RecoveryVerificationConflict(ValueError):
    pass


def execute_recovery_plan(
    session: Session,
    incident: Incident,
    plan: RecoveryPlan,
    executor: RecoveryExecutor,
) -> RecoveryAttemptResult:
    if plan.policy_decision == "DENIED" or plan.approval_status not in {
        "APPROVED",
        "NOT_REQUIRED",
    }:
        raise RecoveryExecutionUnavailable("Recovery plan is not approved")
    if plan.action_type not in executor.capabilities:
        raise RecoveryExecutionUnavailable(f"Recovery executor does not support {plan.action_type}")

    existing = session.exec(
        select(RecoveryAttempt).where(RecoveryAttempt.plan_id == plan.id)
    ).one_or_none()
    if existing is not None:
        return RecoveryAttemptResult(attempt=existing, duplicate=True)

    run = session.get(PipelineRun, incident.pipeline_run_id)
    if run is None:
        raise RuntimeError(f"Incident {incident.id} references an unknown pipeline run")

    idempotency_key = sha256(
        f"{plan.policy_version}:{plan.id}:{plan.action_type}".encode()
    ).hexdigest()
    attempt = RecoveryAttempt(
        incident_id=incident.id,
        plan_id=plan.id,
        provider=executor.provider,
        action_type=plan.action_type,
        attempt_number=1,
        status="PENDING",
        idempotency_key=idempotency_key,
        started_at=datetime.now(UTC),
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)

    request = RecoveryRequest(
        incident_id=incident.id,
        attempt_id=attempt.id,
        project_ref=run.project_ref,
        branch=run.branch,
        action_type=plan.action_type,
        parameters=plan.parameters,
        idempotency_key=idempotency_key,
    )
    try:
        dispatch = executor.execute(request)
    except RecoveryExecutorError as exc:
        attempt.status = "FAILED"
        attempt.result_details = {"error": str(exc)}
        attempt.finished_at = datetime.now(UTC)
        record_recovery_event(
            session,
            incident_id=incident.id,
            plan_id=plan.id,
            attempt_id=attempt.id,
            event_type="EXECUTION_FAILED",
            actor="control-plane",
            details={"error": str(exc)},
        )
        session.add(attempt)
        session.commit()
        raise RecoveryDispatchUnavailable(str(exc)) from exc

    attempt.status = "DISPATCHED"
    attempt.external_reference = dispatch.external_reference
    attempt.result_details = dispatch.details
    record_recovery_event(
        session,
        incident_id=incident.id,
        plan_id=plan.id,
        attempt_id=attempt.id,
        event_type="EXECUTION_DISPATCHED",
        actor="control-plane",
        details={
            "provider": executor.provider,
            "external_reference": dispatch.external_reference,
        },
    )
    session.add(attempt)
    session.commit()
    session.refresh(attempt)
    return RecoveryAttemptResult(attempt=attempt, duplicate=False)


def verify_recovery_attempt(
    session: Session,
    incident: Incident,
    attempt: RecoveryAttempt,
    *,
    idempotency_key: str,
    verification_status: str,
    external_reference: str,
    details: dict[str, object],
) -> RecoveryAttemptResult:
    if attempt.idempotency_key != idempotency_key:
        raise RecoveryVerificationConflict("Recovery verification key does not match")
    if attempt.external_reference != external_reference:
        raise RecoveryVerificationConflict("Recovery external reference does not match")

    if attempt.verification_status is not None:
        if (
            attempt.verification_status == verification_status
            and attempt.verification_details == details
        ):
            return RecoveryAttemptResult(attempt=attempt, duplicate=True)
        raise RecoveryVerificationConflict("Conflicting recovery verification")
    if attempt.status != "DISPATCHED":
        raise RecoveryVerificationConflict("Recovery attempt is not awaiting verification")

    now = datetime.now(UTC)
    attempt.verification_status = verification_status
    attempt.verification_details = details
    attempt.finished_at = now
    if verification_status == "PASSED":
        attempt.status = "VERIFIED"
        incident.status = "RESOLVED"
        event_type = "VERIFICATION_PASSED"
    else:
        attempt.status = "FAILED"
        incident.status = "ACTION_REQUIRED"
        event_type = "VERIFICATION_FAILED"
    incident.updated_at = now
    record_recovery_event(
        session,
        incident_id=incident.id,
        plan_id=attempt.plan_id,
        attempt_id=attempt.id,
        event_type=event_type,
        actor=attempt.provider,
        details={"external_reference": external_reference, **details},
    )
    session.add(attempt)
    session.add(incident)
    session.commit()
    session.refresh(attempt)
    return RecoveryAttemptResult(attempt=attempt, duplicate=False)
