import secrets
from collections.abc import Iterator, Sequence
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session

from dataops_control_plane.services.evidence import EvidenceSource
from dataops_control_plane.services.pipeline_logs import PipelineLogStore


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

_agent_bearer = HTTPBearer(auto_error=False)


def require_agent_token(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_agent_bearer)],
) -> None:
    configured_token = request.app.state.settings.agent_token
    if configured_token is None:
        return

    supplied_token = credentials.credentials if credentials is not None else ""
    if not secrets.compare_digest(configured_token.get_secret_value(), supplied_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing agent token",
            headers={"WWW-Authenticate": "Bearer"},
        )
