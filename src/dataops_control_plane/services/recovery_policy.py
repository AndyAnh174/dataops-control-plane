import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlmodel import Session, select

from dataops_control_plane.api.schemas import RCARecommendedAction, RecoveryActionType
from dataops_control_plane.domain.models import Incident, RCAReport, RecoveryPlan
from dataops_control_plane.services.recovery_audit import record_recovery_event

POLICY_VERSION = "recovery-v1"
MINIMUM_CONFIDENCE = 0.80


class RecoveryPlanUnavailable(ValueError):
    pass


class RecoveryPlanAlreadyDecided(ValueError):
    pass


@dataclass(frozen=True)
class RecoveryPlanResult:
    plan: RecoveryPlan
    duplicate: bool


def create_recovery_plan(session: Session, incident: Incident) -> RecoveryPlanResult:
    report = session.exec(
        select(RCAReport)
        .where(RCAReport.incident_id == incident.id)
        .order_by(RCAReport.created_at.desc())
    ).first()
    if report is None:
        raise RecoveryPlanUnavailable("A validated RCA report is required")

    existing = session.exec(
        select(RecoveryPlan).where(
            RecoveryPlan.rca_report_id == report.id,
            RecoveryPlan.policy_version == POLICY_VERSION,
        )
    ).one_or_none()
    if existing is not None:
        return RecoveryPlanResult(plan=existing, duplicate=True)

    recommendation = RCARecommendedAction.model_validate(report.recommended_action)
    risk_level = _risk_level(recommendation.type)
    policy_decision, approval_status, reasons = _evaluate(report, recommendation)
    plan = RecoveryPlan(
        incident_id=incident.id,
        rca_report_id=report.id,
        action_type=recommendation.type.value,
        parameters=dict(recommendation.parameters),
        risk_level=risk_level,
        policy_decision=policy_decision,
        approval_status=approval_status,
        decision_reasons=reasons,
        policy_version=POLICY_VERSION,
        created_at=datetime.now(UTC),
    )
    session.add(plan)
    session.flush()
    record_recovery_event(
        session,
        incident_id=incident.id,
        plan_id=plan.id,
        event_type="PLAN_CREATED",
        actor="policy-engine",
        details={
            "policy_version": POLICY_VERSION,
            "policy_decision": policy_decision,
            "action_type": recommendation.type.value,
        },
    )
    session.commit()
    session.refresh(plan)
    return RecoveryPlanResult(plan=plan, duplicate=False)


def approve_recovery_plan(session: Session, plan: RecoveryPlan, *, actor: str) -> RecoveryPlan:
    _require_pending(plan)
    if plan.policy_decision != "REQUIRE_APPROVAL":
        raise RecoveryPlanUnavailable("Recovery plan is not eligible for approval")
    plan.approval_status = "APPROVED"
    plan.approved_by = actor
    plan.decided_at = datetime.now(UTC)
    session.add(plan)
    record_recovery_event(
        session,
        incident_id=plan.incident_id,
        plan_id=plan.id,
        event_type="PLAN_APPROVED",
        actor=actor,
    )
    session.commit()
    session.refresh(plan)
    return plan


def reject_recovery_plan(
    session: Session,
    plan: RecoveryPlan,
    *,
    actor: str,
    reason: str,
) -> RecoveryPlan:
    _require_pending(plan)
    plan.approval_status = "REJECTED"
    plan.approved_by = actor
    plan.decided_at = datetime.now(UTC)
    session.add(plan)
    record_recovery_event(
        session,
        incident_id=plan.incident_id,
        plan_id=plan.id,
        event_type="PLAN_REJECTED",
        actor=actor,
        details={"reason": reason},
    )
    session.commit()
    session.refresh(plan)
    return plan


def _require_pending(plan: RecoveryPlan) -> None:
    if plan.approval_status != "PENDING":
        raise RecoveryPlanAlreadyDecided("Recovery plan has already been decided")


def _evaluate(
    report: RCAReport,
    recommendation: RCARecommendedAction,
) -> tuple[str, str, list[str]]:
    reasons: list[str] = []
    if report.analysis_status != "VALIDATED":
        reasons.append("RCA report is not validated")
    if report.confidence < MINIMUM_CONFIDENCE:
        reasons.append(f"RCA confidence {report.confidence:.2f} is below {MINIMUM_CONFIDENCE:.2f}")
    if report.missing_information:
        reasons.append("RCA report still has missing information")
    if recommendation.type in {RecoveryActionType.ESCALATE, RecoveryActionType.NO_ACTION}:
        reasons.append("Recommendation is not an executable recovery action")
    if (
        recommendation.type == RecoveryActionType.ROLLBACK_IMAGE
        and not _has_immutable_rollback_parameters(recommendation.parameters)
    ):
        reasons.append("Rollback requires immutable web/api image tags and a full commit revision")
    if reasons:
        return "DENIED", "NOT_REQUIRED", reasons

    return (
        "REQUIRE_APPROVAL",
        "PENDING",
        ["Mutating recovery actions require explicit human approval"],
    )


def _risk_level(action_type: RecoveryActionType) -> str:
    return {
        RecoveryActionType.RETRY: "LOW",
        RecoveryActionType.QUARANTINE: "MEDIUM",
        RecoveryActionType.ROLLBACK_IMAGE: "HIGH",
        RecoveryActionType.CREATE_PR: "MEDIUM",
        RecoveryActionType.ESCALATE: "LOW",
        RecoveryActionType.NO_ACTION: "LOW",
    }[action_type]


def _has_immutable_rollback_parameters(parameters: dict[str, object]) -> bool:
    image_pattern = re.compile(r"^.+:sha-[0-9a-f]{40}$")
    revision_pattern = re.compile(r"^[0-9a-f]{40}$")
    web_image = parameters.get("target_web_image")
    api_image = parameters.get("target_api_image")
    revision = parameters.get("revision")
    return (
        isinstance(web_image, str)
        and image_pattern.fullmatch(web_image) is not None
        and isinstance(api_image, str)
        and image_pattern.fullmatch(api_image) is not None
        and isinstance(revision, str)
        and revision_pattern.fullmatch(revision) is not None
    )
