from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.ranking import (
    StoryRankedResponse,
    StoryRankingExplanation,
    StoryRankingRunRequest,
    StoryRankingRunResponse,
)
from app.infrastructure.database.session import get_db
from app.services.ranking_service import StoryRankingService

router = APIRouter(prefix="/ranking", tags=["Story Ranking"])


@router.get(
    "/stories",
    response_model=List[StoryRankedResponse],
    summary="List ranked stories",
)
async def list_ranked_stories(
    limit: int = Query(20, ge=1, le=100, description="Max stories to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    min_score: Optional[float] = Query(None, ge=0.0, le=1.0, description="Optional minimum ranking score cutoff"),
    session: AsyncSession = Depends(get_db),
) -> List[StoryRankedResponse]:
    """Retrieve stories ordered descending by ranking score."""
    service = StoryRankingService(session=session)
    return await service.list_ranked_stories(limit=limit, offset=offset, min_score=min_score)


@router.get(
    "/stories/{story_id}",
    response_model=StoryRankingExplanation,
    summary="Get story ranking explanation",
)
async def get_story_ranking_explanation(
    story_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> StoryRankingExplanation:
    """Retrieve full transparent breakdown of all 6 signals and weighted total score for a story."""
    service = StoryRankingService(session=session)
    return await service.rank_story(story_id)


@router.post(
    "/run",
    response_model=StoryRankingRunResponse,
    summary="Trigger bounded story ranking execution",
)
async def run_story_ranking(
    request: StoryRankingRunRequest = StoryRankingRunRequest(),
    session: AsyncSession = Depends(get_db),
) -> StoryRankingRunResponse:
    """Execute a bounded batch story ranking pass across active stories."""
    service = StoryRankingService(session=session)
    return await service.rank_batch(
        limit=request.limit,
        force_recalculate=request.force_recalculate,
    )
