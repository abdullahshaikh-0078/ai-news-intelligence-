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
from app.ai.providers.mock_provider import MockAIProvider


@pytest.fixture(autouse=True)
def mock_gemini_in_ai_api(monkeypatch):
    """Ensure AI API tests use MockAIProvider to remain hermetic and deterministic."""
    monkeypatch.setattr("app.services.ai_service.GeminiProvider", MockAIProvider)


@pytest.fixture
async def api_test_source(db_session: AsyncSession) -> Source:
    source_repo = SourceRepository(db_session)
    source = await source_repo.add(
        Source(
            name="API Test Source",
            slug=f"api-test-src-{uuid.uuid4().hex[:8]}",
            type="RSS",
            url=f"https://example.com/api-feed-{uuid.uuid4().hex[:8]}.xml",
            enabled=True,
        )
    )
    return source


@pytest.mark.asyncio
async def test_api_process_single_content_item(
    async_client: AsyncClient,
    db_session: AsyncSession,
    api_test_source: Source,
):
    """Test POST /api/v1/ai/process/{content_id} processes and returns structured AI results."""
    content_repo = ContentRepository(db_session)
    now = datetime.now(timezone.utc)

    item = await content_repo.add(
        ContentItem(
            source_id=api_test_source.id,
            canonical_url=f"https://example.com/api-item-{uuid.uuid4().hex[:6]}",
            title="Next Frontiers in Multi-Agent Reasoning Systems",
            summary="Novel architecture enabling multi-agent coordination with emergent planning.",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            processing_state="PENDING",
        )
    )

    response = await async_client.post(
        f"/api/v1/ai/process/{item.id}",
        json={"force": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["content_id"] == str(item.id)
    assert data["status"] == "COMPLETED"
    assert data["ai_summary"] is not None
    assert len(data["ai_key_points"]) >= 2
    assert len(data["ai_topics"]) >= 1
    assert 0.0 <= data["ai_relevance_score"] <= 1.0
    assert data["has_embedding"] is True
    assert data["embedding_dim"] == 1536


@pytest.mark.asyncio
async def test_api_process_single_not_found(async_client: AsyncClient):
    """Test POST /api/v1/ai/process/{content_id} returns 404 for non-existent item."""
    random_id = uuid.uuid4()
    response = await async_client.post(
        f"/api/v1/ai/process/{random_id}",
        json={"force": False},
    )
    assert response.status_code == 404
    data = response.json()
    assert "not found" in data["detail"].lower()


@pytest.mark.asyncio
async def test_api_process_batch_content(
    async_client: AsyncClient,
    db_session: AsyncSession,
    api_test_source: Source,
):
    """Test POST /api/v1/ai/process triggers batch processing."""
    content_repo = ContentRepository(db_session)
    now = datetime.now(timezone.utc)

    for i in range(2):
        await content_repo.add(
            ContentItem(
                source_id=api_test_source.id,
                canonical_url=f"https://example.com/batch-api-{uuid.uuid4().hex[:6]}",
                title=f"Batch Article {i}",
                summary=f"Summary for batch article {i}",
                published_at=now,
                fetched_at=now,
                content_hash=uuid.uuid4().hex,
                content_type=ContentType.ARTICLE.value,
                processing_state="PENDING",
            )
        )

    response = await async_client.post(
        "/api/v1/ai/process",
        json={"limit": 10, "force": False},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_candidates"] >= 2
    assert data["completed"] >= 2
    assert data["failed"] == 0


@pytest.mark.asyncio
async def test_api_get_status(async_client: AsyncClient):
    """Test GET /api/v1/ai/status returns processing state metrics."""
    response = await async_client.get("/api/v1/ai/status")
    assert response.status_code == 200
    data = response.json()
    assert "pending" in data
    assert "processing" in data
    assert "completed" in data
    assert "failed" in data
    assert "total" in data
    assert data["total"] >= 0
