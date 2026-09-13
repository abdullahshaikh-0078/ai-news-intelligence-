from datetime import datetime, timezone
import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.content_item import ContentType
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.source import Source
from app.infrastructure.models.story import Story
from app.infrastructure.repositories.content_repo import ContentRepository
from app.infrastructure.repositories.source_repo import SourceRepository


@pytest.fixture
async def api_test_source(db_session: AsyncSession) -> Source:
    source_repo = SourceRepository(db_session)
    source = await source_repo.add(
        Source(
            name="API Test Source",
            slug=f"api-src-{uuid.uuid4().hex[:8]}",
            type="RSS",
            url=f"https://example.com/api-{uuid.uuid4().hex[:8]}.xml",
            reliability_score=0.95,
            enabled=True,
        )
    )
    return source


@pytest.mark.asyncio
async def test_api_ranking_endpoints(
    async_client: AsyncClient,
    db_session: AsyncSession,
    api_test_source: Source,
):
    """Test GET /ranking/stories, GET /ranking/stories/{id}, and POST /ranking/run."""
    content_repo = ContentRepository(db_session)
    now = datetime.now(timezone.utc)

    item = await content_repo.add(
        ContentItem(
            source_id=api_test_source.id,
            canonical_url=f"https://example.com/rank-api-{uuid.uuid4().hex[:6]}",
            title="Next Gen Foundation Model Release",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            ai_relevance_score=0.88,
        )
    )
    story = Story(
        headline="Next Gen Foundation Model Release",
        status="ACTIVE",
        ranking_score=0.85,
        canonical_content_item_id=item.id,
        category="Foundational Models",
        published_at=now,
    )
    db_session.add(story)
    await db_session.flush()

    # 1. GET /api/v1/ranking/stories
    res1 = await async_client.get("/api/v1/ranking/stories?limit=10")
    assert res1.status_code == 200
    stories_list = res1.json()
    assert isinstance(stories_list, list)
    found = next((s for s in stories_list if s["story_id"] == str(story.id)), None)
    assert found is not None
    assert found["ranking_score"] == 0.85

    # 2. GET /api/v1/ranking/stories/{story_id}
    res2 = await async_client.get(f"/api/v1/ranking/stories/{story.id}")
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["story_id"] == str(story.id)
    assert "signals" in data2
    assert "relevance" in data2["signals"]
    assert "authority" in data2["signals"]
    assert "recency" in data2["signals"]
    assert "total_score" in data2

    # 3. GET /api/v1/ranking/stories/{fake_id} -> 404
    fake_id = str(uuid.uuid4())
    res_fake = await async_client.get(f"/api/v1/ranking/stories/{fake_id}")
    assert res_fake.status_code == 404

    # 4. POST /api/v1/ranking/run
    res_run = await async_client.post(
        "/api/v1/ranking/run",
        json={"limit": 10, "force_recalculate": True},
    )
    assert res_run.status_code == 200
    data_run = res_run.json()
    assert "stories_scanned" in data_run
    assert "stories_ranked" in data_run
    assert data_run["stories_ranked"] >= 1


@pytest.mark.asyncio
async def test_api_curation_endpoints(
    async_client: AsyncClient,
    db_session: AsyncSession,
    api_test_source: Source,
):
    """Test POST /curation/run and GET /curation/stories."""
    content_repo = ContentRepository(db_session)
    now = datetime.now(timezone.utc)

    item = await content_repo.add(
        ContentItem(
            source_id=api_test_source.id,
            canonical_url=f"https://example.com/cur-api-{uuid.uuid4().hex[:6]}",
            title="Curated Landmark AI Breakthrough",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
        )
    )
    story = Story(
        headline="Curated Landmark AI Breakthrough",
        status="ACTIVE",
        ranking_score=0.92,
        canonical_content_item_id=item.id,
        category="Breakthroughs",
        published_at=now,
    )
    db_session.add(story)
    await db_session.flush()

    # 1. POST /api/v1/curation/run
    cur_run = await async_client.post(
        "/api/v1/curation/run",
        json={
            "limit": 10,
            "min_score": 0.40,
            "max_per_topic": 2,
            "max_per_source": 2,
            "diversity_enabled": True,
        },
    )
    assert cur_run.status_code == 200
    run_telemetry = cur_run.json()
    assert "curated_count" in run_telemetry
    assert run_telemetry["curated_count"] >= 1

    # 2. GET /api/v1/curation/stories
    cur_list = await async_client.get("/api/v1/curation/stories?limit=10")
    assert cur_list.status_code == 200
    feed = cur_list.json()
    assert isinstance(feed, list)
    curated_ids = [s["story_id"] for s in feed]
    assert str(story.id) in curated_ids
