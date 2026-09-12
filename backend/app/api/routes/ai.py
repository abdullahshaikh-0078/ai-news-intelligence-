from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.schemas.ai import (
    AIBatchProcessRequest,
    AIBatchProcessResponse,
    AIProcessRequest,
    AIProcessResponse,
    AIStatusSummaryResponse,
)
from app.infrastructure.database.session import get_db
from app.infrastructure.repositories.content_repo import ContentRepository
from app.services.ai_service import AIProcessingService

router = APIRouter(prefix="/ai", tags=["AI Processing"])


@router.post(
    "/process/{content_id}",
    response_model=AIProcessResponse,
    summary="Trigger AI processing for a specific content item",
)
async def process_content_item(
    content_id: UUID,
    request: AIProcessRequest = AIProcessRequest(),
    session: AsyncSession = Depends(get_db),
) -> AIProcessResponse:
    """Run AI analysis and vector embedding generation for an individual ContentItem."""
    content_repo = ContentRepository(session)
    item = await content_repo.get_by_id(content_id)
    if not item:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ContentItem with ID {content_id} not found.",
        )

    service = AIProcessingService(session=session)
    try:
        updated = await service.process_item(item, force=request.force)
        emb_dim = len(updated.embedding) if updated.embedding else None
        return AIProcessResponse(
            content_id=updated.id,
            title=updated.title,
            status=updated.processing_state,
            ai_summary=updated.ai_summary,
            ai_key_points=updated.ai_key_points or [],
            ai_topics=updated.ai_topics or [],
            ai_relevance_score=updated.ai_relevance_score,
            ai_model=updated.ai_model,
            has_embedding=updated.embedding is not None,
            embedding_dim=emb_dim,
            ai_processed_at=updated.ai_processed_at,
            message="Content item successfully processed by AI engine.",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI processing failed: {exc.__class__.__name__}",
        )


@router.post(
    "/process",
    response_model=AIBatchProcessResponse,
    summary="Trigger batch AI processing for pending content items",
)
async def process_batch_content(
    request: AIBatchProcessRequest = AIBatchProcessRequest(),
    session: AsyncSession = Depends(get_db),
) -> AIBatchProcessResponse:
    """Execute concurrent, bounded AI processing across pending content items."""
    service = AIProcessingService(session=session)
    return await service.process_pending_batch(limit=request.limit, force=request.force)


@router.get(
    "/status",
    response_model=AIStatusSummaryResponse,
    summary="Get overall AI processing status summary",
)
async def get_ai_processing_status(
    session: AsyncSession = Depends(get_db),
) -> AIStatusSummaryResponse:
    """Get system-wide summary counts of content items by processing state."""
    service = AIProcessingService(session=session)
    counts = await service.get_processing_status_summary()
    return AIStatusSummaryResponse(**counts)
