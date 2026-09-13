from datetime import datetime, timezone
import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.content_item import ContentType
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.source import Source
from app.infrastructure.repositories.content_repo import ContentRepository
from app.infrastructure.repositories.source_repo import SourceRepository


def _make_unit_vector(val: float = 1.0) -> list[float]:
    v = [0.0] * 1536
    v[0] = val
    v[1] = 0.5
    norm = sum(x * x for x in v) ** 0.5
    return [x / norm for x in v]


@pytest.fixture
async def api_dedup_source(db_session: AsyncSession) -> Source:
    source_repo = SourceRepository(db_session)
    source = await source_repo.add(
        Source(
            name="API Dedup Test Source",
            slug=f"api-dedup-{uuid.uuid4().hex[:8]}",
            type="RSS",
            url=f"https://example.com/feed-{uuid.uuid4().hex[:8]}.xml",
            enabled=True,
        )
    )
    return source


@pytest.mark.asyncio
async def test_api_get_similar_content(
    async_client: AsyncClient,
    db_session: AsyncSession,
    api_dedup_source: Source,
):
    """Test GET /api/v1/dedup/similar/{content_id} returns structured matches."""
    content_repo = ContentRepository(db_session)
    now = datetime.now(timezone.utc)
    vec1 = _make_unit_vector(1.0)
    vec2 = list(vec1)
    vec2[2] = 0.05
    norm = sum(x * x for x in vec2) ** 0.5
    vec2 = [x / norm for x in vec2]

    item1 = await content_repo.add(
        ContentItem(
            source_id=api_dedup_source.id,
            canonical_url=f"https://example.com/api-sim-1-{uuid.uuid4().hex[:6]}",
            title="Reasoning LLMs Overview",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            embedding=vec1,
        )
    )

    item2 = await content_repo.add(
        ContentItem(
            source_id=api_dedup_source.id,
            canonical_url=f"https://example.com/api-sim-2-{uuid.uuid4().hex[:6]}",
            title="Reasoning LLMs Summary",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            embedding=vec2,
        )
    )

    response = await async_client.get(f"/api/v1/dedup/similar/{item1.id}?limit=5")
    assert response.status_code == 200
    data = response.json()
    assert data["content_id"] == str(item1.id)
    assert data["has_embedding"] is True
    assert data["total_matches"] >= 1
    assert data["matches"][0]["matched_content_id"] == str(item2.id)


@pytest.mark.asyncio
async def test_api_check_duplicates_endpoint(
    async_client: AsyncClient,
    db_session: AsyncSession,
    api_dedup_source: Source,
):
    """Test POST /api/v1/dedup/check/{content_id} combines deterministic and semantic matches."""
    content_repo = ContentRepository(db_session)
    now = datetime.now(timezone.utc)
    shared_hash = uuid.uuid4().hex

    item = await content_repo.add(
        ContentItem(
            source_id=api_dedup_source.id,
            canonical_url=f"https://example.com/api-chk-{uuid.uuid4().hex[:6]}",
            title="Check Target Item",
            published_at=now,
            fetched_at=now,
            content_hash=shared_hash,
            content_type=ContentType.ARTICLE.value,
        )
    )

    await content_repo.add(
        ContentItem(
            source_id=api_dedup_source.id,
            canonical_url=f"https://example.com/api-chk-dup-{uuid.uuid4().hex[:6]}",
            title="Check Duplicate Item",
            published_at=now,
            fetched_at=now,
            content_hash=shared_hash,
            content_type=ContentType.ARTICLE.value,
        )
    )

    response = await async_client.post(f"/api/v1/dedup/check/{item.id}?persist=true")
    assert response.status_code == 200
    data = response.json()
    assert data["content_id"] == str(item.id)
    assert len(data["deterministic_duplicates"]) >= 1
    assert data["top_classification"] == "EXACT_DUPLICATE"
    assert data["persisted"] is True


@pytest.mark.asyncio
async def test_api_run_batch_dedup(
    async_client: AsyncClient,
):
    """Test POST /api/v1/dedup/run triggers bounded batch analysis."""
    response = await async_client.post(
        "/api/v1/dedup/run",
        json={"limit": 5, "persist": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert "total_scanned" in data
    assert "items_with_embedding" in data
    assert "duplicate_pairs_identified" in data


@pytest.mark.asyncio
async def test_api_dedup_404_not_found(
    async_client: AsyncClient,
):
    """Test 404 returned for nonexistent ContentItem ID."""
    missing_id = uuid.uuid4()
    response = await async_client.get(f"/api/v1/dedup/similar/{missing_id}")
    assert response.status_code == 404
