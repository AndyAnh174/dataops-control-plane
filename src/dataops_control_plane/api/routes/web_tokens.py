from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, status

from dataops_control_plane.api.dependencies import CurrentWebUserDep, SameOriginDep, SessionDep
from dataops_control_plane.api.schemas import (
    IntegrationTokenCreate,
    IntegrationTokenCreated,
    IntegrationTokenListResponse,
    IntegrationTokenRead,
)
from dataops_control_plane.services.web_projects import (
    IntegrationTokenAlreadyExists,
    IntegrationTokenNotFound,
    ProjectNotFound,
    WorkspacePermissionDenied,
    create_integration_token,
    list_integration_tokens,
    revoke_integration_token,
)

router = APIRouter(prefix="/api/v1/projects", tags=["web-project-tokens"])


@router.post("/{project_id}/tokens", status_code=status.HTTP_201_CREATED)
def post_integration_token(
    project_id: Annotated[UUID, Path(description="Project ID")],
    payload: IntegrationTokenCreate,
    user: CurrentWebUserDep,
    same_origin: SameOriginDep,
    session: SessionDep,
) -> IntegrationTokenCreated:
    try:
        created = create_integration_token(
            session,
            project_id=project_id,
            user_id=user.id,
            name=payload.name,
            scopes=[scope.value for scope in payload.scopes],
            expires_in_days=payload.expires_in_days,
        )
    except ProjectNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WorkspacePermissionDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except IntegrationTokenAlreadyExists as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return IntegrationTokenCreated.model_validate(
        {**IntegrationTokenRead.model_validate(created.record).model_dump(), "token": created.token}
    )


@router.get("/{project_id}/tokens")
def get_integration_tokens(
    project_id: Annotated[UUID, Path(description="Project ID")],
    user: CurrentWebUserDep,
    session: SessionDep,
) -> IntegrationTokenListResponse:
    try:
        tokens = list_integration_tokens(session, project_id=project_id, user_id=user.id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return IntegrationTokenListResponse(
        items=[IntegrationTokenRead.model_validate(item) for item in tokens]
    )


@router.delete(
    "/{project_id}/tokens/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_integration_token(
    project_id: Annotated[UUID, Path(description="Project ID")],
    token_id: Annotated[UUID, Path(description="Integration token ID")],
    user: CurrentWebUserDep,
    same_origin: SameOriginDep,
    session: SessionDep,
) -> None:
    try:
        revoke_integration_token(
            session,
            project_id=project_id,
            token_id=token_id,
            user_id=user.id,
        )
    except ProjectNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except IntegrationTokenNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WorkspacePermissionDenied as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
