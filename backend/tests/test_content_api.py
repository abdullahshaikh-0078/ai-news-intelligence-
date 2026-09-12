import uuid
from datetime import datetime, timezone
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.content_item import ContentType
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.source import Source
from app.infrastructure.repositories.content_repo import ContentRepository
from app.infrastructure.repositories.source_repo import SourceRepository


@pytest.fixture
async def content_test_source(db_session: AsyncSession) -> Source:
    source_repo = SourceRepository(db_session)
    source = await source_repo.add(
        Source(
            name="Content API Source",
            slug=f"content-src-{uuid.uuid4().hex[:8]}",
            type="RSS",
            url=f"https://example.com/rss-{uuid.uuid4().hex[:8]}.xml",
            enabled=True,
        )
    )
    return source


@pytest.mark.asyncio
async def test_api_list_content_paginated(
    async_client: AsyncClient,
    db_session: AsyncSession,
    content_test_source: Source,
):
    """Test GET /api/v1/content pagination and filtering."""
    content_repo = ContentRepository(db_session)
    now = datetime.now(timezone.utc)

    # Insert test items
    item_art = await content_repo.add(
        ContentItem(
            source_id=content_test_source.id,
            title=f"Article Test {uuid.uuid4().hex[:6]}",
            canonical_url=f"https://example.com/art-{uuid.uuid4().hex[:8]}",
            content_type=ContentType.ARTICLE.value,
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            ai_relevance_score=0.9,
            ai_topics=["AI", "LLM"],
        )
    )
    item_vid = await content_repo.add(
        ContentItem(
            source_id=content_test_source.id,
            title=f"Video Test {uuid.uuid4().hex[:6]}",
            canonical_url=f"https://example.com/vid-{uuid.uuid4().hex[:8]}",
            content_type=ContentType.VIDEO.value,
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            ai_relevance_score=0.7,
            ai_topics=["Robotics"],
        )
    )

    # 1. Unfiltered paginated request
    res = await async_client.get("/api/v1/content?page=1&page_size=10")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert "page_size" in data
    assert "total_pages" in data
    assert data["page"] == 1
    assert data["page_size"] == 10
    assert data["total"] >= 2
    assert len(data["items"]) >= 2

    # Verify fields in item
    matched = [it for it in data["items"] if it["id"] == str(item_art.id)]
    assert len(matched) == 1
    assert matched[0]["source_name"] == "Content API Source"
    assert matched[0]["content_type"] == ContentType.ARTICLE.value
    assert matched[0]["ai_topics"] == ["AI", "LLM"]

    # 2. Filter by content_type
    res_filtered = await async_client.get(f"/api/v1/content?content_type={ContentType.VIDEO.value}")
    assert res_filtered.status_code == 200
    filtered_data = res_filtered.json()
    assert all(it["content_type"] == ContentType.VIDEO.value for it in filtered_data["items"])
    vid_ids = [it["id"] for it in filtered_data["items"]]
    assert str(item_vid.id) in vid_ids
    assert str(item_art.id) not in vid_ids


@pytest.mark.asyncio
async def test_api_get_content_by_id(
    async_client: AsyncClient,
    db_session: AsyncSession,
    content_test_source: Source,
):
    """Test GET /api/v1/content/{id} with source join and 404 behavior."""
    content_repo = ContentRepository(db_session)
    now = datetime.now(timezone.utc)

    item = await content_repo.add(
        ContentItem(
            source_id=content_test_source.id,
            title="Specific Content Item",
            canonical_url=f"https://example.com/spec-{uuid.uuid4().hex[:8]}",
            content_type=ContentType.ARTICLE.value,
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            ai_summary="Grounded item summary.",
        )
    )

    # 1. Existing item
    res = await async_client.get(f"/api/v1/content/{item.id}")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == str(item.id)
    assert data["title"] == "Specific Content Item"
    assert data["source_name"] == "Content API Source"
    assert data["ai_summary"] == "Grounded item summary."

    # 2. Non-existent item
    random_id = uuid.uuid4()
    res_404 = await async_client.get(f"/api/v1/content/{random_id}")
    assert res_404.status_code == 404
    assert res_404.json()["error"]["code"] == "NOT_FOUND"
