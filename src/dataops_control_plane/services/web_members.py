from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from dataops_control_plane.domain.models import AppUser, WorkspaceMember
from dataops_control_plane.services.web_identity import hash_password
from dataops_control_plane.services.web_projects import (
    require_workspace_membership,
)


class WorkspaceMemberAlreadyExists(ValueError):
    pass


class WorkspaceMemberNotFound(ValueError):
    pass


class WorkspaceMemberChangeForbidden(ValueError):
    pass


class InitialPasswordRequired(ValueError):
    pass


@dataclass(frozen=True)
class WorkspaceMemberDetails:
    membership: WorkspaceMember
    user: AppUser


def list_workspace_members(
    session: Session,
    *,
    workspace_id: UUID,
    actor_user_id: UUID,
) -> list[WorkspaceMemberDetails]:
    _require_owner(session, workspace_id=workspace_id, actor_user_id=actor_user_id)
    rows = session.exec(
        select(WorkspaceMember, AppUser)
        .join(AppUser, AppUser.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .order_by(WorkspaceMember.created_at.asc())
    ).all()
    return [WorkspaceMemberDetails(membership=membership, user=user) for membership, user in rows]


def create_workspace_member(
    session: Session,
    *,
    workspace_id: UUID,
    actor_user_id: UUID,
    email: str,
    initial_password: str | None,
    role: str,
) -> WorkspaceMemberDetails:
    _require_owner(session, workspace_id=workspace_id, actor_user_id=actor_user_id)
    user = session.exec(select(AppUser).where(AppUser.email == email)).first()
    if user is None:
        if initial_password is None:
            raise InitialPasswordRequired("Initial password is required for a new user")
        user = AppUser(
            email=email,
            password_hash=hash_password(initial_password),
            created_at=datetime.now(UTC),
        )
        session.add(user)
        session.flush()
    elif (
        session.exec(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user.id,
            )
        ).first()
        is not None
    ):
        raise WorkspaceMemberAlreadyExists("User is already a member of this workspace")

    membership = WorkspaceMember(
        workspace_id=workspace_id,
        user_id=user.id,
        role=role,
        created_at=datetime.now(UTC),
    )
    session.add(membership)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise WorkspaceMemberAlreadyExists("User is already a member of this workspace") from exc
    session.refresh(membership)
    return WorkspaceMemberDetails(membership=membership, user=user)


def update_workspace_member(
    session: Session,
    *,
    workspace_id: UUID,
    membership_id: UUID,
    actor_user_id: UUID,
    role: str,
) -> WorkspaceMemberDetails:
    _require_owner(session, workspace_id=workspace_id, actor_user_id=actor_user_id)
    membership = _get_membership(session, workspace_id=workspace_id, membership_id=membership_id)
    if membership.user_id == actor_user_id and membership.role != role:
        raise WorkspaceMemberChangeForbidden("You cannot demote your own workspace membership")
    if membership.role == "OWNER" and role != "OWNER":
        _require_another_owner(session, workspace_id=workspace_id, membership_id=membership.id)
    membership.role = role
    session.add(membership)
    session.commit()
    session.refresh(membership)
    user = session.get(AppUser, membership.user_id)
    if user is None:
        raise RuntimeError("Workspace membership references an unknown user")
    return WorkspaceMemberDetails(membership=membership, user=user)


def remove_workspace_member(
    session: Session,
    *,
    workspace_id: UUID,
    membership_id: UUID,
    actor_user_id: UUID,
) -> None:
    _require_owner(session, workspace_id=workspace_id, actor_user_id=actor_user_id)
    membership = _get_membership(session, workspace_id=workspace_id, membership_id=membership_id)
    if membership.user_id == actor_user_id:
        raise WorkspaceMemberChangeForbidden("You cannot remove your own workspace membership")
    if membership.role == "OWNER":
        _require_another_owner(session, workspace_id=workspace_id, membership_id=membership.id)
    session.delete(membership)
    session.commit()


def _require_owner(session: Session, *, workspace_id: UUID, actor_user_id: UUID) -> None:
    require_workspace_membership(
        session,
        workspace_id=workspace_id,
        user_id=actor_user_id,
        allowed_roles={"OWNER"},
    )


def _get_membership(
    session: Session, *, workspace_id: UUID, membership_id: UUID
) -> WorkspaceMember:
    membership = session.get(WorkspaceMember, membership_id)
    if membership is None or membership.workspace_id != workspace_id:
        raise WorkspaceMemberNotFound("Workspace member not found")
    return membership


def _require_another_owner(session: Session, *, workspace_id: UUID, membership_id: UUID) -> None:
    another_owner = session.exec(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.role == "OWNER",
            WorkspaceMember.id != membership_id,
        )
    ).first()
    if another_owner is None:
        raise WorkspaceMemberChangeForbidden("A workspace must retain at least one owner")
