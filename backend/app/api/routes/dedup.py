from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, Query, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.deduplication import (
    BatchDeduplicationRequest,
    BatchDeduplicationResponse,
    ContentSimilarResponse,
    DuplicateCheckResponse,
)
from app.infrastructure.database.session import get_db
from app.services.dedup_service import SemanticDeduplicationService

router = APIRouter(prefix="/dedup", tags=["Semantic Deduplication"])


@router.get(
    "/similar/{content_id}",
    response_model=ContentSimilarResponse,
    summary="Find nearest semantically similar ContentItems",
)
async def get_similar_content(
    content_id: UUID,
    limit: int = Query(10, ge=1, le=50, description="Max similar items to return"),
    min_similarity: Optional[float] = Query(None, ge=-1.0, le=1.0, description="Optional minimum cosine similarity filter"),
    session: AsyncSession = Depends(get_db),
) -> ContentSimilarResponse:
    """Retrieve nearest ContentItems via pgvector cosine distance without modifying any records."""
    service = SemanticDeduplicationService(session=session)
    return await service.find_similar(
        content_id=content_id,
        limit=limit,
        min_similarity=min_similarity,
    )


@router.post(
    "/check/{content_id}",
    response_model=DuplicateCheckResponse,
    summary="Check deterministic and semantic duplicates for a ContentItem",
)
async def check_duplicates(
    content_id: UUID,
    persist: bool = Query(False, description="Persist identified duplicate pairs to database"),
    session: AsyncSession = Depends(get_db),
) -> DuplicateCheckResponse:
    """Evaluate duplicate likelihood across deterministic signals and semantic embeddings."""
    service = SemanticDeduplicationService(session=session)
    return await service.check_duplicates(
        content_id=content_id,
        persist=persist,
    )


@router.post(
    "/run",
    response_model=BatchDeduplicationResponse,
    summary="Execute a bounded batch deduplication run",
)
async def run_batch_deduplication(
    request: BatchDeduplicationRequest = BatchDeduplicationRequest(),
    session: AsyncSession = Depends(get_db),
) -> BatchDeduplicationResponse:
    """Scan a bounded batch of embedded items to identify and record duplicate relationships."""
    service = SemanticDeduplicationService(session=session)
    return await service.run_batch(
        limit=request.limit,
        persist=request.persist,
    )
