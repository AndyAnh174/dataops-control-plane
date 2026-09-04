from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Request, status

from dataops_control_plane.api.dependencies import CurrentWebUserDep, SessionDep
from dataops_control_plane.api.schemas import GitHubOnboardingRead
from dataops_control_plane.services.provider_onboarding import build_github_onboarding
from dataops_control_plane.services.web_projects import ProjectNotFound, require_project_access

router = APIRouter(prefix="/api/v1/projects", tags=["web-project-onboarding"])


@router.get("/{project_id}/onboarding/github")
def get_github_onboarding(
    project_id: Annotated[UUID, Path(description="Project ID")],
    request: Request,
    user: CurrentWebUserDep,
    session: SessionDep,
) -> GitHubOnboardingRead:
    try:
        project = require_project_access(session, project_id=project_id, user_id=user.id)
    except ProjectNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if project.provider != "github":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="GitHub onboarding is only available for GitHub projects",
        )
    public_url = request.app.state.settings.public_url or str(request.base_url).rstrip("/")
    return build_github_onboarding(project, public_url=public_url)
