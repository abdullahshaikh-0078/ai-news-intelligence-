from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.ranking import StoryRankingExplanation
from app.domain.models.story import (
    StoryClusterRequest,
    StoryClusterResponse,
    StoryDetailResponse,
    StoryRefreshResponse,
    StoryWithItemsResponse,
)
from app.infrastructure.database.session import get_db
from app.services.ranking_service import StoryRankingService
from app.services.story_service import StoryClusteringService

router = APIRouter(prefix="/stories", tags=["Stories & Clustering"])


@router.get(
    "",
    response_model=List[StoryDetailResponse],
    summary="List synthesized stories",
)
async def list_stories(
    status: Optional[str] = Query(None, description="Optional status filter (e.g. ACTIVE, UPDATED)"),
    limit: int = Query(20, ge=1, le=100, description="Max stories to return"),
    offset: int = Query(0, ge=0, description="Offset pagination"),
    session: AsyncSession = Depends(get_db),
) -> List[StoryDetailResponse]:
    """Retrieve paginated list of clustered stories ordered by recency."""
    service = StoryClusteringService(session=session)
    return await service.list_stories(status=status, limit=limit, offset=offset)


@router.get(
    "/{story_id}",
    response_model=StoryDetailResponse,
    summary="Get story by ID",
)
async def get_story(
    story_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> StoryDetailResponse:
    """Retrieve detailed information for an individual Story."""
    service = StoryClusteringService(session=session)
    detailed = await service.get_story_details(story_id)
    return StoryDetailResponse(
        id=detailed.id,
        title=detailed.title,
        summary=detailed.summary,
        key_takeaway=detailed.key_takeaway,
        why_it_matters=detailed.why_it_matters,
        status=detailed.status,
        category=detailed.category,
        importance_score=detailed.importance_score,
        ranking_score=detailed.ranking_score,
        is_curated=detailed.is_curated,
        canonical_content_item_id=detailed.canonical_content_item_id,
        article_count=detailed.article_count,
        published_at=detailed.published_at,
        created_at=detailed.created_at,
        updated_at=detailed.updated_at,
    )


@router.get(
    "/{story_id}/items",
    response_model=StoryWithItemsResponse,
    summary="Get story with member evidence items",
)
async def get_story_with_items(
    story_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> StoryWithItemsResponse:
    """Retrieve a Story with full source provenance of all associated ContentItems."""
    service = StoryClusteringService(session=session)
    return await service.get_story_details(story_id)


@router.get(
    "/{story_id}/ranking",
    response_model=StoryRankingExplanation,
    summary="Get story ranking breakdown and explanation",
)
async def get_story_ranking(
    story_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> StoryRankingExplanation:
    """Retrieve the transparent 6-signal ranking breakdown and weighted total score for a story."""
    ranking_service = StoryRankingService(session=session)
    return await ranking_service.rank_story(story_id)


@router.post(
    "/cluster",
    response_model=StoryClusterResponse,
    summary="Trigger bounded story clustering run",
)
async def trigger_story_clustering(
    request: StoryClusterRequest = StoryClusterRequest(),
    session: AsyncSession = Depends(get_db),
) -> StoryClusterResponse:
    """Execute bounded multi-signal clustering across unassigned ContentItems."""
    service = StoryClusteringService(session=session)
    return await service.cluster_unassigned_candidates(
        limit=request.limit,
        window_hours=request.window_hours,
        similarity_threshold=request.similarity_threshold,
    )


@router.post(
    "/{story_id}/refresh",
    response_model=StoryRefreshResponse,
    summary="Refresh story synthesis and representative item",
)
async def refresh_story(
    story_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> StoryRefreshResponse:
    """Re-evaluate member signal and regenerate grounded synthesis for a Story."""
    service = StoryClusteringService(session=session)
    return await service.refresh_story(story_id)
