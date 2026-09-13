import uuid
from datetime import datetime, timezone
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.content_item import ContentType
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.source import Source
from app.infrastructure.models.story import Story
from app.infrastructure.models.story_content_item import StoryContentItem
from app.infrastructure.repositories.content_repo import ContentRepository
from app.infrastructure.repositories.source_repo import SourceRepository
from app.infrastructure.repositories.story_repo import StoryRepository


@pytest.fixture
async def overview_test_source(db_session: AsyncSession) -> Source:
    source_repo = SourceRepository(db_session)
    source = await source_repo.add(
        Source(
            name="Overview Test Source",
            slug=f"overview-src-{uuid.uuid4().hex[:8]}",
            type="RSS",
            url=f"https://example.com/ov-{uuid.uuid4().hex[:8]}.xml",
            reliability_score=0.9,
            enabled=True,
        )
    )
    return source


@pytest.mark.asyncio
async def test_api_system_overview(
    async_client: AsyncClient,
    db_session: AsyncSession,
    overview_test_source: Source,
):
    """Test GET /api/v1/overview returns live platform metrics."""
    content_repo = ContentRepository(db_session)
    story_repo = StoryRepository(db_session)
    now = datetime.now(timezone.utc)

    # Seed an item and a story
    item = await content_repo.add(
        ContentItem(
            source_id=overview_test_source.id,
            title="Overview Test Item",
            canonical_url=f"https://example.com/ov-{uuid.uuid4().hex[:8]}",
            content_type=ContentType.ARTICLE.value,
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            ai_relevance_score=0.85,
        )
    )
    story = await story_repo.create_story(
        title="Overview Test Story",
        canonical_item_id=item.id,
        category="Test Category",
        status="ACTIVE",
    )
    story.is_curated = True
    story.ranking_score = 0.88
    db_session.add(story)
    await db_session.flush()

    res = await async_client.get("/api/v1/overview")
    assert res.status_code == 200
    data = res.json()

    assert "total_content_items" in data
    assert data["total_content_items"] >= 1
    assert "total_stories" in data
    assert data["total_stories"] >= 1
    assert "active_sources" in data
    assert data["active_sources"] >= 1
    assert "curated_stories" in data
    assert data["curated_stories"] >= 1
    assert "content_type_distribution" in data
    assert ContentType.ARTICLE.value in data["content_type_distribution"]
    assert "embedding_dimensions" in data
    assert data["embedding_dimensions"] == 1536
    assert "ai_provider" in data
    assert "Google Gemini" in data["ai_provider"]
    assert "version" in data


@pytest.mark.asyncio
async def test_api_story_ranking_explanation(
    async_client: AsyncClient,
    db_session: AsyncSession,
    overview_test_source: Source,
):
    """Test GET /api/v1/stories/{id}/ranking provides full 6-signal breakdown."""
    content_repo = ContentRepository(db_session)
    now = datetime.now(timezone.utc)

    item = await content_repo.add(
        ContentItem(
            source_id=overview_test_source.id,
            canonical_url=f"https://example.com/rank-{uuid.uuid4().hex[:8]}",
            title="Story Ranking Breakdown Test",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            ai_relevance_score=0.92,
        )
    )

    story = Story(
        headline="Story Ranking Breakdown Test",
        status="ACTIVE",
        ranking_score=0.85,
        canonical_content_item_id=item.id,
        category="Artificial Intelligence",
        published_at=now,
    )
    db_session.add(story)
    await db_session.flush()

    assoc = StoryContentItem(
        story_id=story.id,
        content_item_id=item.id,
        is_canonical=True,
        confidence_score=0.95,
    )
    db_session.add(assoc)
    await db_session.flush()

    # 1. Successful ranking query
    res = await async_client.get(f"/api/v1/stories/{story.id}/ranking")
    assert res.status_code == 200
    breakdown = res.json()
    assert breakdown["story_id"] == str(story.id)
    assert "total_score" in breakdown
    assert "signals" in breakdown
    assert "weights_sum" in breakdown
    assert "recency" in breakdown["signals"]
    assert "weight" in breakdown["signals"]["recency"]
    assert "coverage" in breakdown["signals"] or "source_diversity" in breakdown["signals"] or len(breakdown["signals"]) >= 5

    # 2. Non-existent story query
    random_id = uuid.uuid4()
    res_404 = await async_client.get(f"/api/v1/stories/{random_id}/ranking")
    assert res_404.status_code == 404
    assert res_404.json()["error"]["code"] == "NOT_FOUND"
