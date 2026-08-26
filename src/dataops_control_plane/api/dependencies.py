from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlmodel import Session

from dataops_control_plane.services.pipeline_logs import PipelineLogStore


def get_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def get_log_store(request: Request) -> PipelineLogStore:
    return request.app.state.log_store


LogStoreDep = Annotated[PipelineLogStore, Depends(get_log_store)]
