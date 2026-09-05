import hashlib
import secrets
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session, select

from dataops_control_plane.domain.models import (
    AppUser,
    IntegrationToken,
    PipelineRun,
    PlatformState,
    Project,
)
from dataops_control_plane.services.evidence import EvidenceSource
from dataops_control_plane.services.pipeline_logs import PipelineLogStore
from dataops_control_plane.services.rca_agent import RCAAgent
from dataops_control_plane.services.recovery_execution import RecoveryExecutor
from dataops_control_plane.services.retrieval import HybridRetriever
from dataops_control_plane.services.web_identity import get_user_for_session


def get_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def get_log_store(request: Request) -> PipelineLogStore:
    return request.app.state.log_store


LogStoreDep = Annotated[PipelineLogStore, Depends(get_log_store)]


def get_evidence_sources(request: Request) -> Sequence[EvidenceSource]:
    return request.app.state.evidence_sources


EvidenceSourcesDep = Annotated[Sequence[EvidenceSource], Depends(get_evidence_sources)]


def get_hybrid_retriever(request: Request) -> HybridRetriever:
    return request.app.state.hybrid_retriever


HybridRetrieverDep = Annotated[HybridRetriever, Depends(get_hybrid_retriever)]


def get_rca_agent(request: Request) -> RCAAgent:
    return request.app.state.rca_agent


RCAAgentDep = Annotated[RCAAgent, Depends(get_rca_agent)]


def get_recovery_executor(request: Request) -> RecoveryExecutor:
    return request.app.state.recovery_executor


RecoveryExecutorDep = Annotated[RecoveryExecutor, Depends(get_recovery_executor)]


def require_web_user(request: Request, session: SessionDep) -> AppUser:
    token = request.cookies.get(request.app.state.settings.web_session_cookie_name, "")
    user = get_user_for_session(session, token) if token else None
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return user


CurrentWebUserDep = Annotated[AppUser, Depends(require_web_user)]


def require_web_user_after_bootstrap(request: Request, session: SessionDep) -> AppUser | None:
    if session.get(PlatformState, 1) is None:
        return None
    return require_web_user(request, session)


WebReadUserDep = Annotated[AppUser | None, Depends(require_web_user_after_bootstrap)]


def require_same_origin(request: Request) -> None:
    supplied_origin = request.headers.get("origin")
    if supplied_origin is None:
        return
    configured_url = request.app.state.settings.public_url
    if configured_url:
        parsed = urlsplit(configured_url)
        expected_origin = f"{parsed.scheme}://{parsed.netloc}"
    else:
        expected_origin = str(request.base_url).rstrip("/")
    if not secrets.compare_digest(supplied_origin, expected_origin):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-origin mutation is not allowed",
        )


SameOriginDep = Annotated[None, Depends(require_same_origin)]

_agent_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AgentPrincipal:
    authentication_type: str
    user_id: UUID | None = None
    project_id: UUID | None = None
    project_ref: str | None = None
    provider: str | None = None
    scopes: frozenset[str] = frozenset()


def require_agent_token(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_agent_bearer)],
    session: SessionDep,
) -> AgentPrincipal:
    configured_token = request.app.state.settings.agent_token
    supplied_token = credentials.credentials if credentials is not None else ""
    if configured_token is not None and secrets.compare_digest(
        configured_token.get_secret_value(), supplied_token
    ):
        return AgentPrincipal(authentication_type="legacy-instance-token")

    if supplied_token:
        token_hash = hashlib.sha256(supplied_token.encode("utf-8")).hexdigest()
        integration_token = session.exec(
            select(IntegrationToken).where(IntegrationToken.secret_hash == token_hash)
        ).first()
        if integration_token is not None:
            expires_at = integration_token.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if integration_token.revoked_at is None and expires_at > datetime.now(UTC):
                project = session.get(Project, integration_token.project_id)
                if project is not None:
                    integration_token.last_used_at = datetime.now(UTC)
                    session.add(integration_token)
                    session.commit()
                    return AgentPrincipal(
                        authentication_type="project-token",
                        project_id=project.id,
                        project_ref=project.project_ref,
                        provider=project.provider,
                        scopes=frozenset(integration_token.scopes),
                    )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing agent token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_auth_is_required = (
        configured_token is not None
        or session.exec(select(IntegrationToken.id).limit(1)).first() is not None
    )
    if not token_auth_is_required:
        return AgentPrincipal(authentication_type="development-open")

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing agent token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_agent_scope(principal: AgentPrincipal, scope: str) -> None:
    if principal.authentication_type == "project-token" and scope not in principal.scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Agent token is missing required scope: {scope}",
        )


def require_agent_run_access(
    principal: AgentPrincipal,
    run: PipelineRun,
    *,
    scope: str,
) -> None:
    require_agent_scope(principal, scope)
    if principal.authentication_type == "project-token" and (
        principal.project_ref != run.project_ref or principal.provider != run.provider
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token does not belong to this pipeline run",
        )


def require_operator_run_access(
    principal: AgentPrincipal,
    session: Session,
    run: PipelineRun,
) -> None:
    if principal.authentication_type != "web-session" or principal.user_id is None:
        return
    from dataops_control_plane.services.web_projects import (
        ProjectNotFound,
        require_pipeline_run_access,
    )

    try:
        require_pipeline_run_access(
            session,
            run=run,
            user_id=principal.user_id,
            allowed_roles={"OWNER", "OPERATOR"},
        )
    except ProjectNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Incident not found",
        ) from exc


AgentPrincipalDep = Annotated[AgentPrincipal, Depends(require_agent_token)]


def require_operator_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_agent_bearer)],
    session: SessionDep,
) -> AgentPrincipal:
    if credentials is not None:
        principal = require_agent_token(request, credentials, session)
        if principal.authentication_type == "project-token":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Project tokens cannot perform operator actions",
            )
        return principal

    session_token = request.cookies.get(request.app.state.settings.web_session_cookie_name, "")
    user = get_user_for_session(session, session_token) if session_token else None
    if user is not None:
        return AgentPrincipal(authentication_type="web-session", user_id=user.id)

    return require_agent_token(request, credentials, session)


OperatorPrincipalDep = Annotated[AgentPrincipal, Depends(require_operator_principal)]
