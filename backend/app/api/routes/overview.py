from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.schemas.overview import OverviewResponse
from app.infrastructure.database.session import get_db
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.source import Source
from app.infrastructure.models.story import Story

router = APIRouter(prefix="/overview", tags=["System Overview & Metrics"])


@router.get(
    "",
    response_model=OverviewResponse,
    status_code=status.HTTP_200_OK,
    summary="Get real-time system intelligence overview and metrics",
)
async def get_system_overview(
    session: AsyncSession = Depends(get_db),
) -> OverviewResponse:
    """Retrieve live statistics computed directly from the PostgreSQL intelligence database."""
    # 1. Total ContentItems
    total_items = (await session.execute(select(func.count(ContentItem.id)))).scalar_one()

    # 2. Total Stories
    total_stories = (await session.execute(select(func.count(Story.id)))).scalar_one()

    # 3. Active Sources
    active_sources = (
        await session.execute(select(func.count(Source.id)).where(Source.enabled.is_(True)))
    ).scalar_one()

    # 4. Curated Stories
    curated_stories = (
        await session.execute(select(func.count(Story.id)).where(Story.is_curated.is_(True)))
    ).scalar_one()

    # 5. Content Type Distribution
    type_res = await session.execute(
        select(ContentItem.content_type, func.count(ContentItem.id)).group_by(ContentItem.content_type)
    )
    content_type_dist = {row[0]: row[1] for row in type_res.all()}

    # 6. Latest Timestamps
    latest_ingested = (await session.execute(select(func.max(ContentItem.fetched_at)))).scalar_one_or_none()
    latest_story = (await session.execute(select(func.max(Story.published_at)))).scalar_one_or_none()

    return OverviewResponse(
        total_content_items=total_items,
        total_stories=total_stories,
        active_sources=active_sources,
        curated_stories=curated_stories,
        content_type_distribution=content_type_dist,
        latest_ingested_at=latest_ingested,
        latest_story_at=latest_story,
        embedding_dimensions=settings.GEMINI_EMBEDDING_DIMENSIONS,
        ai_provider=f"Google Gemini ({settings.GEMINI_CHAT_MODEL} / {settings.GEMINI_EMBEDDING_MODEL})",
        version=settings.APP_VERSION,
    )
