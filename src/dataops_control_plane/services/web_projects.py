import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from dataops_control_plane.domain.models import (
    IntegrationToken,
    PipelineRun,
    Project,
    Workspace,
    WorkspaceMember,
)


class WorkspaceNotFound(Exception):
    pass


class WorkspacePermissionDenied(Exception):
    pass


class ProjectAlreadyExists(Exception):
    pass


class ProjectNotFound(Exception):
    pass


class ProjectConfirmationMismatch(Exception):
    pass


class IntegrationTokenAlreadyExists(Exception):
    pass


class IntegrationTokenNotFound(Exception):
    pass


@dataclass(frozen=True)
class CreatedIntegrationToken:
    record: IntegrationToken
    token: str


def require_workspace_membership(
    session: Session,
    *,
    workspace_id: UUID,
    user_id: UUID,
    allowed_roles: set[str] | None = None,
) -> WorkspaceMember:
    if session.get(Workspace, workspace_id) is None:
        raise WorkspaceNotFound("Workspace not found")
    membership = session.exec(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    ).first()
    if membership is None:
        raise WorkspaceNotFound("Workspace not found")
    if allowed_roles is not None and membership.role not in allowed_roles:
        raise WorkspacePermissionDenied("Workspace role does not permit this action")
    return membership


def create_project(
    session: Session,
    *,
    workspace_id: UUID,
    user_id: UUID,
    name: str,
    provider: str,
    project_ref: str,
    default_branch: str,
) -> Project:
    require_workspace_membership(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        allowed_roles={"OWNER", "OPERATOR"},
    )
    project = Project(
        workspace_id=workspace_id,
        name=name,
        provider=provider,
        project_ref=project_ref,
        default_branch=default_branch,
        created_at=datetime.now(UTC),
    )
    session.add(project)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ProjectAlreadyExists("Project is already registered in this workspace") from exc
    session.refresh(project)
    return project


def list_projects(
    session: Session,
    *,
    workspace_id: UUID,
    user_id: UUID,
) -> list[Project]:
    require_workspace_membership(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    return list(
        session.exec(
            select(Project)
            .where(Project.workspace_id == workspace_id)
            .order_by(Project.created_at.asc())
        ).all()
    )


def delete_project(
    session: Session,
    *,
    workspace_id: UUID,
    project_id: UUID,
    user_id: UUID,
    confirm_project_ref: str,
) -> None:
    require_workspace_membership(
        session,
        workspace_id=workspace_id,
        user_id=user_id,
        allowed_roles={"OWNER"},
    )
    project = session.get(Project, project_id)
    if project is None or project.workspace_id != workspace_id:
        raise ProjectNotFound("Project not found")
    if not secrets.compare_digest(confirm_project_ref, project.project_ref):
        raise ProjectConfirmationMismatch("Project confirmation does not match")
    tokens = session.exec(
        select(IntegrationToken).where(IntegrationToken.project_id == project.id)
    ).all()
    for token in tokens:
        session.delete(token)
    session.flush()
    session.delete(project)
    session.commit()


def require_project_access(
    session: Session,
    *,
    project_id: UUID,
    user_id: UUID,
    allowed_roles: set[str] | None = None,
) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise ProjectNotFound("Project not found")
    try:
        require_workspace_membership(
            session,
            workspace_id=project.workspace_id,
            user_id=user_id,
            allowed_roles=allowed_roles,
        )
    except WorkspaceNotFound as exc:
        raise ProjectNotFound("Project not found") from exc
    return project


def require_pipeline_run_access(
    session: Session,
    *,
    run: PipelineRun,
    user_id: UUID,
    allowed_roles: set[str] | None = None,
) -> Project:
    candidates = session.exec(
        select(Project).where(
            Project.provider == run.provider,
            Project.project_ref == run.project_ref,
        )
    ).all()
    for project in candidates:
        try:
            require_workspace_membership(
                session,
                workspace_id=project.workspace_id,
                user_id=user_id,
                allowed_roles=allowed_roles,
            )
        except (WorkspaceNotFound, WorkspacePermissionDenied):
            continue
        return project
    raise ProjectNotFound("Pipeline run not found")


def create_integration_token(
    session: Session,
    *,
    project_id: UUID,
    user_id: UUID,
    name: str,
    scopes: list[str],
    expires_in_days: int,
) -> CreatedIntegrationToken:
    require_project_access(
        session,
        project_id=project_id,
        user_id=user_id,
        allowed_roles={"OWNER", "OPERATOR"},
    )
    now = datetime.now(UTC)
    raw_token = f"dop_{secrets.token_urlsafe(32)}"
    record = IntegrationToken(
        project_id=project_id,
        name=name,
        token_prefix=raw_token[:12],
        secret_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        scopes=scopes,
        expires_at=now + timedelta(days=expires_in_days),
        created_by=user_id,
        created_at=now,
    )
    session.add(record)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise IntegrationTokenAlreadyExists(
            "An integration token with this name already exists"
        ) from exc
    session.refresh(record)
    return CreatedIntegrationToken(record=record, token=raw_token)


def list_integration_tokens(
    session: Session,
    *,
    project_id: UUID,
    user_id: UUID,
) -> list[IntegrationToken]:
    require_project_access(session, project_id=project_id, user_id=user_id)
    return list(
        session.exec(
            select(IntegrationToken)
            .where(IntegrationToken.project_id == project_id)
            .order_by(IntegrationToken.created_at.asc())
        ).all()
    )


def revoke_integration_token(
    session: Session,
    *,
    project_id: UUID,
    token_id: UUID,
    user_id: UUID,
) -> None:
    require_project_access(
        session,
        project_id=project_id,
        user_id=user_id,
        allowed_roles={"OWNER", "OPERATOR"},
    )
    token = session.get(IntegrationToken, token_id)
    if token is None or token.project_id != project_id:
        raise IntegrationTokenNotFound("Integration token not found")
    if token.revoked_at is None:
        token.revoked_at = datetime.now(UTC)
        session.add(token)
        session.commit()
