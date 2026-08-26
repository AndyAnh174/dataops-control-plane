from fastapi import APIRouter, Depends, HTTPException, status

from dataops_control_plane.api.dependencies import HybridRetrieverDep, require_agent_token
from dataops_control_plane.api.schemas import (
    HybridSearchFusionRead,
    HybridSearchItemRead,
    HybridSearchRequest,
    HybridSearchResponse,
    KnowledgeDocumentCreate,
    KnowledgeDocumentReceipt,
)
from dataops_control_plane.services.retrieval import (
    EmbeddingUnavailable,
    HybridSearchItem,
    KnowledgeFilter,
    KnowledgeIndexResult,
    KnowledgeStoreUnavailable,
)

router = APIRouter(
    prefix="/api/v1/retrieval",
    tags=["retrieval"],
    dependencies=[Depends(require_agent_token)],
)


@router.post(
    "/documents",
    status_code=status.HTTP_202_ACCEPTED,
)
def index_knowledge_document(
    request: KnowledgeDocumentCreate,
    retriever: HybridRetrieverDep,
) -> KnowledgeDocumentReceipt:
    try:
        result = retriever.index_document(
            document_type=request.document_type,
            title=request.title,
            content=request.content,
            source_uri=request.source_uri,
            project_ref=request.project_ref,
            provider=request.provider,
            incident_type=request.incident_type,
            environment=request.environment,
            version=request.version,
            metadata=request.metadata,
        )
    except (EmbeddingUnavailable, KnowledgeStoreUnavailable) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return _index_receipt(result)


@router.post("/search")
def search_knowledge(
    request: HybridSearchRequest,
    retriever: HybridRetrieverDep,
) -> HybridSearchResponse:
    filters = KnowledgeFilter(
        project_ref=request.filters.project_ref,
        document_types=tuple(request.filters.document_types),
        provider=request.filters.provider,
        incident_type=request.filters.incident_type,
        environment=request.filters.environment,
        created_after=request.filters.created_after,
    )
    try:
        result = retriever.search(request.query, top_k=request.top_k, filters=filters)
    except (EmbeddingUnavailable, KnowledgeStoreUnavailable) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return HybridSearchResponse(
        query=result.query,
        embedding_model=retriever.embedding_model,
        fusion=HybridSearchFusionRead(
            rank_constant=retriever.rank_constant,
            candidate_limit=result.candidate_limit,
        ),
        redaction_count=result.redaction_count,
        items=[_search_item(item) for item in result.items],
    )


def _index_receipt(result: KnowledgeIndexResult) -> KnowledgeDocumentReceipt:
    return KnowledgeDocumentReceipt(
        document_id=result.document.document_id,
        document_type=result.document.document_type,
        checksum=result.document.checksum,
        result=result.result,
        embedding_model=result.document.embedding_model,
        redaction_count=result.redaction_count,
    )


def _search_item(item: HybridSearchItem) -> HybridSearchItemRead:
    document = item.document
    return HybridSearchItemRead(
        document_id=document.document_id,
        document_type=document.document_type,
        title=document.title,
        content=document.content,
        source_uri=document.source_uri,
        project_ref=document.project_ref,
        provider=document.provider,
        incident_id=document.incident_id,
        incident_type=document.incident_type,
        environment=document.environment,
        version=document.version,
        metadata=document.metadata,
        rrf_score=item.rrf_score,
        matched_by=list(item.matched_by),
        keyword_rank=item.keyword_rank,
        keyword_score=item.keyword_score,
        vector_rank=item.vector_rank,
        vector_score=item.vector_score,
    )
