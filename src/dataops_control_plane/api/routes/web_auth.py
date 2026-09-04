from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, Response, status

from dataops_control_plane.api.dependencies import CurrentWebUserDep, SameOriginDep, SessionDep
from dataops_control_plane.api.schemas import (
    AuthContextRead,
    AuthUserRead,
    AuthWorkspaceRead,
    BootstrapCreate,
    LoginCreate,
)
from dataops_control_plane.services.web_identity import (
    BootstrapUnavailable,
    InvalidCredentials,
    authenticate_user,
    bootstrap_owner,
    list_user_workspaces,
    revoke_web_session,
)

router = APIRouter(tags=["web-auth"])


def _set_session_cookie(response: Response, settings, token: str, ttl: timedelta) -> None:
    response.set_cookie(
        key=settings.web_session_cookie_name,
        value=token,
        max_age=int(ttl.total_seconds()),
        httponly=True,
        secure=settings.web_session_cookie_secure,
        samesite="strict",
    )


def _auth_context(session, user) -> AuthContextRead:
    return AuthContextRead(
        user=AuthUserRead(id=user.id, email=user.email),
        workspaces=[
            AuthWorkspaceRead(id=workspace.id, name=workspace.name, role=membership.role)
            for workspace, membership in list_user_workspaces(session, user.id)
        ],
    )


@router.post(
    "/api/v1/auth/bootstrap",
    status_code=status.HTTP_201_CREATED,
)
def bootstrap(
    payload: BootstrapCreate,
    request: Request,
    response: Response,
    same_origin: SameOriginDep,
    session: SessionDep,
) -> AuthContextRead:
    settings = request.app.state.settings
    ttl = timedelta(hours=settings.web_session_ttl_hours)
    try:
        created = bootstrap_owner(
            session,
            email=payload.email,
            password=payload.password,
            workspace_name=payload.workspace_name,
            session_ttl=ttl,
        )
    except BootstrapUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    _set_session_cookie(response, settings, created.session_token, ttl)
    return _auth_context(session, created.user)


@router.get("/api/v1/me")
def get_me(user: CurrentWebUserDep, session: SessionDep) -> AuthContextRead:
    return _auth_context(session, user)


@router.post("/api/v1/auth/login")
def login(
    payload: LoginCreate,
    request: Request,
    response: Response,
    same_origin: SameOriginDep,
    session: SessionDep,
) -> AuthContextRead:
    settings = request.app.state.settings
    ttl = timedelta(hours=settings.web_session_ttl_hours)
    try:
        created = authenticate_user(
            session,
            email=payload.email,
            password=payload.password,
            session_ttl=ttl,
        )
    except InvalidCredentials as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    _set_session_cookie(response, settings, created.session_token, ttl)
    return _auth_context(session, created.user)


@router.post("/api/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    same_origin: SameOriginDep,
    session: SessionDep,
) -> None:
    settings = request.app.state.settings
    token = request.cookies.get(settings.web_session_cookie_name, "")
    if token:
        revoke_web_session(session, token)
    response.delete_cookie(
        key=settings.web_session_cookie_name,
        secure=settings.web_session_cookie_secure,
        httponly=True,
        samesite="strict",
    )
