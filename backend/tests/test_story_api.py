from datetime import datetime, timezone, timedelta
import uuid
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


def _make_unit_vector(val: float = 1.0) -> list[float]:
    v = [0.0] * 1536
    v[0] = val
    v[1] = 0.5
    norm = sum(x * x for x in v) ** 0.5
    return [x / norm for x in v]


@pytest.fixture
async def story_api_source(db_session: AsyncSession) -> Source:
    source_repo = SourceRepository(db_session)
    source = await source_repo.add(
        Source(
            name="Story API Test Source",
            slug=f"story-api-{uuid.uuid4().hex[:8]}",
            type="RSS",
            url=f"https://example.com/story-feed-{uuid.uuid4().hex[:8]}.xml",
            enabled=True,
        )
    )
    return source


@pytest.mark.asyncio
async def test_api_list_stories_empty(async_client: AsyncClient):
    """GET /api/v1/stories returns empty list when no stories exist."""
    response = await async_client.get("/api/v1/stories")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_api_list_and_get_story(
    async_client: AsyncClient,
    db_session: AsyncSession,
    story_api_source: Source,
):
    """Test GET /api/v1/stories and GET /api/v1/stories/{id} with story items."""
    content_repo = ContentRepository(db_session)
    now = datetime.now(timezone.utc)
    vec1 = _make_unit_vector(1.0)

    # 1. Create content items
    item1 = await content_repo.add(
        ContentItem(
            source_id=story_api_source.id,
            canonical_url=f"https://example.com/story-item-1-{uuid.uuid4().hex[:6]}",
            title="DeepSeek V3 Architecture Release",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            summary="DeepSeek releases open-weights model V3.",
            embedding=vec1,
        )
    )
    item2 = await content_repo.add(
        ContentItem(
            source_id=story_api_source.id,
            canonical_url=f"https://example.com/story-item-2-{uuid.uuid4().hex[:6]}",
            title="Analysis: DeepSeek V3 Performance",
            published_at=now + timedelta(minutes=10),
            fetched_at=now + timedelta(minutes=10),
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.RESEARCH_PAPER.value,
            summary="Benchmarking DeepSeek V3 against other frontier models.",
            embedding=vec1,
        )
    )

    # 2. Create Story in DB with StoryContentItems
    story = Story(
        headline="DeepSeek V3 Architecture Release",
        summary="DeepSeek announces V3 with MoE architecture and open weights.",
        status="ACTIVE",
        canonical_content_item_id=item1.id,
        published_at=now,
    )
    db_session.add(story)
    await db_session.flush()

    assoc1 = StoryContentItem(
        story_id=story.id,
        content_item_id=item1.id,
        is_canonical=True,
        confidence_score=1.0,
    )
    assoc2 = StoryContentItem(
        story_id=story.id,
        content_item_id=item2.id,
        is_canonical=False,
        confidence_score=0.92,
    )
    db_session.add_all([assoc1, assoc2])
    item1.story_id = story.id
    item2.story_id = story.id
    await db_session.commit()

    # 3. GET /api/v1/stories
    response = await async_client.get("/api/v1/stories?limit=10")
    assert response.status_code == 200
    stories = response.json()
    assert len(stories) >= 1
    found = next((s for s in stories if s["id"] == str(story.id)), None)
    assert found is not None
    assert found["title"] == "DeepSeek V3 Architecture Release"
    assert found["status"] == "ACTIVE"
    assert found["article_count"] == 2

    # 4. GET /api/v1/stories/{story_id}
    detail_res = await async_client.get(f"/api/v1/stories/{story.id}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["id"] == str(story.id)
    assert detail["title"] == "DeepSeek V3 Architecture Release"
    assert detail["canonical_content_item_id"] == str(item1.id)
    assert detail["article_count"] == 2

    # 5. GET /api/v1/stories/{story_id}/items
    items_res = await async_client.get(f"/api/v1/stories/{story.id}/items")
    assert items_res.status_code == 200
    data = items_res.json()
    items = data["items"]
    assert len(items) == 2
    canonical_entry = next((i for i in items if i["is_canonical"]), None)
    assert canonical_entry is not None
    assert canonical_entry["content_id"] == str(item1.id)
    item_ids = [i["content_id"] for i in items]
    assert str(item1.id) in item_ids
    assert str(item2.id) in item_ids


@pytest.mark.asyncio
async def test_api_story_not_found(async_client: AsyncClient):
    """GET /api/v1/stories/{story_id} and items endpoint return 404 for nonexistent IDs."""
    fake_id = str(uuid.uuid4())
    res1 = await async_client.get(f"/api/v1/stories/{fake_id}")
    assert res1.status_code == 404

    res2 = await async_client.get(f"/api/v1/stories/{fake_id}/items")
    assert res2.status_code == 404


@pytest.mark.asyncio
async def test_api_cluster_endpoint(
    async_client: AsyncClient,
    db_session: AsyncSession,
    story_api_source: Source,
):
    """POST /api/v1/stories/cluster clusters unclustered items via API."""
    content_repo = ContentRepository(db_session)
    now = datetime.now(timezone.utc)
    # Distinct orthogonal vector to avoid matching previous tests
    vec = [0.0] * 1536
    vec[10] = 1.0

    item = await content_repo.add(
        ContentItem(
            source_id=story_api_source.id,
            canonical_url=f"https://example.com/cluster-api-1-{uuid.uuid4().hex[:6]}",
            title="Qwen 2.5 Coding Benchmark Report",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            summary="Qwen 2.5 sets new coding benchmark records.",
            embedding=vec,
        )
    )
    await db_session.commit()

    payload = {
        "limit": 20,
        "window_hours": 72,
        "similarity_threshold": 0.85,
    }
    response = await async_client.post("/api/v1/stories/cluster", json=payload)
    assert response.status_code == 200
    res_data = response.json()
    assert "candidates_scanned" in res_data
    assert "stories_created" in res_data
    assert "stories_updated" in res_data
    assert "items_assigned" in res_data
    assert res_data["candidates_scanned"] >= 1
    assert res_data["stories_created"] >= 1


@pytest.mark.asyncio
async def test_api_refresh_story_endpoint(
    async_client: AsyncClient,
    db_session: AsyncSession,
    story_api_source: Source,
):
    """POST /api/v1/stories/{story_id}/refresh recalculates representative and synthesis."""
    content_repo = ContentRepository(db_session)
    now = datetime.now(timezone.utc)
    vec = _make_unit_vector(1.0)

    item1 = await content_repo.add(
        ContentItem(
            source_id=story_api_source.id,
            canonical_url=f"https://example.com/refresh-1-{uuid.uuid4().hex[:6]}",
            title="Short title",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.COMMUNITY_POST.value,
            embedding=vec,
        )
    )
    item2 = await content_repo.add(
        ContentItem(
            source_id=story_api_source.id,
            canonical_url=f"https://example.com/refresh-2-{uuid.uuid4().hex[:6]}",
            title="Comprehensive Analysis of FlashAttention-3 Kernel Optimization",
            published_at=now + timedelta(minutes=5),
            fetched_at=now + timedelta(minutes=5),
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.RESEARCH_PAPER.value,
            summary="A deep technical breakdown of FlashAttention-3 kernels on Hopper architecture.",
            embedding=vec,
        )
    )

    story = Story(
        headline="Initial Temporary Title",
        summary="Short summary.",
        status="ACTIVE",
        canonical_content_item_id=item1.id,
        published_at=now,
    )
    db_session.add(story)
    await db_session.flush()

    assoc1 = StoryContentItem(story_id=story.id, content_item_id=item1.id, is_canonical=True)
    assoc2 = StoryContentItem(story_id=story.id, content_item_id=item2.id, is_canonical=False)
    db_session.add_all([assoc1, assoc2])
    item1.story_id = story.id
    item2.story_id = story.id
    await db_session.commit()

    refresh_res = await async_client.post(f"/api/v1/stories/{story.id}/refresh")
    assert refresh_res.status_code == 200
    data = refresh_res.json()
    assert data["story_id"] == str(story.id)
    # The higher authoritative PAPER should be selected as new canonical title
    assert data["article_count"] == 2
    assert "FlashAttention-3" in data["title"]


@pytest.mark.asyncio
async def test_api_refresh_story_not_found(async_client: AsyncClient):
    """POST /api/v1/stories/{story_id}/refresh returns 404 for unknown story."""
    fake_id = str(uuid.uuid4())
    res = await async_client.post(f"/api/v1/stories/{fake_id}/refresh")
    assert res.status_code == 404
