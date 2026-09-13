from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, status

from dataops_control_plane.api.dependencies import CurrentWebUserDep, SameOriginDep, SessionDep
from dataops_control_plane.api.schemas import (
    WorkspaceMemberCreate,
    WorkspaceMemberListResponse,
    WorkspaceMemberRead,
    WorkspaceMemberUpdate,
)
from dataops_control_plane.services.web_members import (
    InitialPasswordRequired,
    WorkspaceMemberAlreadyExists,
    WorkspaceMemberChangeForbidden,
    WorkspaceMemberDetails,
    WorkspaceMemberNotFound,
    create_workspace_member,
    list_workspace_members,
    remove_workspace_member,
    update_workspace_member,
)
from dataops_control_plane.services.web_projects import (
    WorkspaceNotFound,
    WorkspacePermissionDenied,
)

router = APIRouter(prefix="/api/v1/workspaces", tags=["web-workspace-members"])


def _member_read(details: WorkspaceMemberDetails) -> WorkspaceMemberRead:
    return WorkspaceMemberRead(
        id=details.membership.id,
        workspace_id=details.membership.workspace_id,
        user_id=details.user.id,
        email=details.user.email,
        role=details.membership.role,
        status=details.user.status,
        created_at=details.membership.created_at,
    )


@router.get("/{workspace_id}/members")
def get_members(
    workspace_id: Annotated[UUID, Path(description="Workspace ID")],
    user: CurrentWebUserDep,
    session: SessionDep,
) -> WorkspaceMemberListResponse:
    try:
        members = list_workspace_members(session, workspace_id=workspace_id, actor_user_id=user.id)
    except WorkspaceNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WorkspacePermissionDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return WorkspaceMemberListResponse(items=[_member_read(item) for item in members])


@router.post("/{workspace_id}/members", status_code=status.HTTP_201_CREATED)
def post_member(
    workspace_id: Annotated[UUID, Path(description="Workspace ID")],
    payload: WorkspaceMemberCreate,
    user: CurrentWebUserDep,
    same_origin: SameOriginDep,
    session: SessionDep,
) -> WorkspaceMemberRead:
    try:
        member = create_workspace_member(
            session,
            workspace_id=workspace_id,
            actor_user_id=user.id,
            email=payload.email,
            initial_password=payload.initial_password,
            role=payload.role.value,
        )
    except WorkspaceNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WorkspacePermissionDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except (WorkspaceMemberAlreadyExists, InitialPasswordRequired) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _member_read(member)


@router.patch("/{workspace_id}/members/{membership_id}")
def patch_member(
    workspace_id: Annotated[UUID, Path(description="Workspace ID")],
    membership_id: Annotated[UUID, Path(description="Workspace membership ID")],
    payload: WorkspaceMemberUpdate,
    user: CurrentWebUserDep,
    same_origin: SameOriginDep,
    session: SessionDep,
) -> WorkspaceMemberRead:
    try:
        member = update_workspace_member(
            session,
            workspace_id=workspace_id,
            membership_id=membership_id,
            actor_user_id=user.id,
            role=payload.role.value,
        )
    except (WorkspaceNotFound, WorkspaceMemberNotFound) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WorkspacePermissionDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except WorkspaceMemberChangeForbidden as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _member_read(member)


@router.delete(
    "/{workspace_id}/members/{membership_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_member(
    workspace_id: Annotated[UUID, Path(description="Workspace ID")],
    membership_id: Annotated[UUID, Path(description="Workspace membership ID")],
    user: CurrentWebUserDep,
    same_origin: SameOriginDep,
    session: SessionDep,
) -> None:
    try:
        remove_workspace_member(
            session,
            workspace_id=workspace_id,
            membership_id=membership_id,
            actor_user_id=user.id,
        )
    except (WorkspaceNotFound, WorkspaceMemberNotFound) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WorkspacePermissionDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except WorkspaceMemberChangeForbidden as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
