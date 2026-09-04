from datetime import UTC, datetime
from uuid import UUID

from sqlmodel import Session

from dataops_control_plane.domain.models import RecoveryAuditEvent


def record_recovery_event(
    session: Session,
    *,
    incident_id: UUID,
    event_type: str,
    actor: str,
    plan_id: UUID | None = None,
    attempt_id: UUID | None = None,
    details: dict[str, object] | None = None,
) -> RecoveryAuditEvent:
    event = RecoveryAuditEvent(
        incident_id=incident_id,
        plan_id=plan_id,
        attempt_id=attempt_id,
        event_type=event_type,
        actor=actor,
        details=details or {},
        created_at=datetime.now(UTC),
    )
    session.add(event)
    return event
