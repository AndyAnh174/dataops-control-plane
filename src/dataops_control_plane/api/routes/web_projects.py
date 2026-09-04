from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, status

from dataops_control_plane.api.dependencies import CurrentWebUserDep, SameOriginDep, SessionDep
from dataops_control_plane.api.schemas import ProjectCreate, ProjectListResponse, ProjectRead
from dataops_control_plane.services.web_projects import (
    ProjectAlreadyExists,
    WorkspaceNotFound,
    WorkspacePermissionDenied,
    create_project,
    list_projects,
)

router = APIRouter(prefix="/api/v1/workspaces", tags=["web-projects"])


@router.post("/{workspace_id}/projects", status_code=status.HTTP_201_CREATED)
def post_project(
    workspace_id: Annotated[UUID, Path(description="Workspace ID")],
    payload: ProjectCreate,
    user: CurrentWebUserDep,
    same_origin: SameOriginDep,
    session: SessionDep,
) -> ProjectRead:
    try:
        project = create_project(
            session,
            workspace_id=workspace_id,
            user_id=user.id,
            name=payload.name,
            provider=payload.provider,
            project_ref=payload.project_ref,
            default_branch=payload.default_branch,
        )
    except WorkspaceNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WorkspacePermissionDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ProjectAlreadyExists as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return ProjectRead.model_validate(project)


@router.get("/{workspace_id}/projects")
def get_projects(
    workspace_id: Annotated[UUID, Path(description="Workspace ID")],
    user: CurrentWebUserDep,
    session: SessionDep,
) -> ProjectListResponse:
    try:
        projects = list_projects(session, workspace_id=workspace_id, user_id=user.id)
    except WorkspaceNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ProjectListResponse(items=[ProjectRead.model_validate(item) for item in projects])
