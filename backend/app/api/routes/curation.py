from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.curation import (
    CuratedStoryResponse,
    StoryCurationRunRequest,
    StoryCurationRunResponse,
)
from app.infrastructure.database.session import get_db
from app.services.curation_service import StoryCurationService

router = APIRouter(prefix="/curation", tags=["Story Curation"])


@router.get(
    "/stories",
    response_model=List[CuratedStoryResponse],
    summary="List curated feed stories",
)
async def list_curated_stories(
    limit: int = Query(20, ge=1, le=100, description="Max curated stories to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    topic: Optional[str] = Query(None, description="Filter curated stories by topic or category"),
    content_type: Optional[str] = Query(None, description="Filter curated stories by content type (e.g. ARTICLE, VIDEO, PAPER)"),
    session: AsyncSession = Depends(get_db),
) -> List[CuratedStoryResponse]:
    """Retrieve editorially curated stories currently surfaced for news digests and feeds with optional topic/content type filters."""
    service = StoryCurationService(session=session)
    return await service.list_curated_stories(
        limit=limit,
        offset=offset,
        topic=topic,
        content_type=content_type,
    )


@router.post(
    "/run",
    response_model=StoryCurationRunResponse,
    summary="Trigger deterministic story curation pass",
)
async def run_story_curation(
    request: StoryCurationRunRequest = StoryCurationRunRequest(),
    session: AsyncSession = Depends(get_db),
) -> StoryCurationRunResponse:
    """Execute a deterministic editorial curation pass enforcing diversity caps and quality cutoffs."""
    service = StoryCurationService(session=session)
    return await service.curate_feed(
        limit=request.limit,
        min_score=request.min_score,
        max_per_topic=request.max_per_topic,
        max_per_source=request.max_per_source,
        diversity_enabled=request.diversity_enabled,
    )
