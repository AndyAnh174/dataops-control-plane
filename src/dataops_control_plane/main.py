from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, create_engine

from dataops_control_plane.api.routes.events import router as events_router
from dataops_control_plane.api.routes.incidents import router as incidents_router
from dataops_control_plane.api.routes.logs import router as logs_router
from dataops_control_plane.api.routes.retrieval import router as retrieval_router
from dataops_control_plane.api.routes.runs import router as runs_router
from dataops_control_plane.config import Settings
from dataops_control_plane.services.elasticsearch_knowledge import ElasticsearchKnowledgeStore
from dataops_control_plane.services.elasticsearch_logs import ElasticsearchPipelineLogStore
from dataops_control_plane.services.evidence import EvidenceSource
from dataops_control_plane.services.github_evidence import GitHubCommitEvidenceSource
from dataops_control_plane.services.ollama_embeddings import OllamaEmbeddingProvider
from dataops_control_plane.services.pipeline_logs import PipelineLogStore
from dataops_control_plane.services.retrieval import HybridRetriever


class HealthResponse(BaseModel):
    status: str
    service: str


def create_app(
    engine: Engine | None = None,
    log_store: PipelineLogStore | None = None,
    evidence_sources: Sequence[EvidenceSource] | None = None,
    hybrid_retriever: HybridRetriever | None = None,
) -> FastAPI:
    settings = Settings()
    database_engine = engine or create_engine(settings.database_url, pool_pre_ping=True)
    pipeline_log_store = log_store or ElasticsearchPipelineLogStore.from_settings(settings)
    provider_evidence_sources = (
        list(evidence_sources)
        if evidence_sources is not None
        else [GitHubCommitEvidenceSource.from_settings(settings)]
    )
    knowledge_retriever = hybrid_retriever or HybridRetriever(
        ElasticsearchKnowledgeStore.from_settings(settings),
        OllamaEmbeddingProvider.from_settings(settings),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        SQLModel.metadata.create_all(database_engine)
        try:
            yield
        finally:
            close = getattr(pipeline_log_store, "close", None)
            if close is not None:
                close()
            for evidence_source in provider_evidence_sources:
                evidence_source.close()
            knowledge_retriever.close()

    application = FastAPI(
        title="DataOps Control Plane",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.engine = database_engine
    application.state.log_store = pipeline_log_store
    application.state.evidence_sources = provider_evidence_sources
    application.state.hybrid_retriever = knowledge_retriever
    application.state.settings = settings

    @application.get("/health", tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok", service="dataops-control-plane")

    application.include_router(events_router)
    application.include_router(incidents_router)
    application.include_router(logs_router)
    application.include_router(retrieval_router)
    application.include_router(runs_router)
    return application


app = create_app()
