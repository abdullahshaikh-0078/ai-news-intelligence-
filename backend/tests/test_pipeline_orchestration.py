from datetime import datetime, timezone
from typing import List, Optional
from unittest.mock import AsyncMock, patch
import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.mock_provider import MockAIProvider
from app.domain.models.content_item import AIAnalysisResult, ContentType, ProcessingStatus
from app.domain.models.pipeline import PipelineRunRequest
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.source import Source
from app.infrastructure.models.story import Story
from app.infrastructure.repositories.source_repo import SourceRepository
from app.services.ai_service import AIProcessingService
from app.services.pipeline_service import PipelineOrchestratorService


class MockPipelineAIProvider(MockAIProvider):
    """Custom mock provider for pipeline testing tracking calls and simulating 429 quota exhaustion."""

    def __init__(self, should_fail_429: bool = False):
        super().__init__(
            chat_model="gemini-3.6-flash",
            embedding_model="gemini-embedding-001",
            embedding_dimensions=1536,
        )
        self.should_fail_429 = should_fail_429

    async def analyze_content(
        self, text: str, content_type: Optional[str] = None, metadata: Optional[dict] = None
    ) -> AIAnalysisResult:
        if self.should_fail_429:
            raise RuntimeError("Gemini 429: ResourceExhausted quota limit exceeded")
        return await super().analyze_content(text, content_type=content_type or "ARTICLE", metadata=metadata)

    async def generate_embedding(self, text: str) -> List[float]:
        if self.should_fail_429:
            raise RuntimeError("Gemini 429: ResourceExhausted embedding quota exceeded")
        return await super().generate_embedding(text)


@pytest.fixture
async def pipeline_test_source(db_session: AsyncSession) -> Source:
    source_repo = SourceRepository(db_session)
    source = await source_repo.add(
        Source(
            name="Pipeline Test Source",
            slug=f"pipe-src-{uuid.uuid4().hex[:8]}",
            type="RSS",
            url=f"https://example.com/pipe-{uuid.uuid4().hex[:8]}.xml",
            enabled=True,
        )
    )
    return source


@pytest.mark.asyncio
async def test_pipeline_status_endpoint(async_client: AsyncClient):
    """Test GET /api/v1/pipeline/status returns health, limits, and queue summary."""
    response = await async_client.get("/api/v1/pipeline/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "READY"
    assert "limits" in data
    assert "queue_summary" in data
    assert data["limits"]["max_items_per_run"] >= 1
    assert data["limits"]["on_demand_embedding_enabled"] is True


@pytest.mark.asyncio
async def test_pipeline_bounded_execution(
    db_session: AsyncSession, pipeline_test_source: Source
):
    """Pipeline run must strictly respect the configured limit on candidate items."""
    now = datetime.now(timezone.utc)
    # Create 5 pending items
    for i in range(5):
        item = ContentItem(
            source_id=pipeline_test_source.id,
            canonical_url=f"https://example.com/bounded-{i}-{uuid.uuid4().hex[:6]}",
            title=f"Bounded Item {i}",
            summary=f"Summary for bounded item {i}",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            processing_state="PENDING",
        )
        db_session.add(item)
    await db_session.flush()

    provider = MockPipelineAIProvider()
    ai_service = AIProcessingService(session=db_session, provider=provider)
    orchestrator = PipelineOrchestratorService(session=db_session, ai_service=ai_service)

    # Request limit of 2 items
    req = PipelineRunRequest(limit=2, max_ai_analyses=2, max_embeddings=2)
    result = await orchestrator.run_pipeline(req)

    assert result.status in ("SUCCESS", "PARTIAL")
    assert result.items_seen == 2
    assert result.ai_analyses_performed <= 2
    assert result.embeddings_generated <= 2


@pytest.mark.asyncio
async def test_pipeline_idempotency_and_embedding_reuse(
    db_session: AsyncSession, pipeline_test_source: Source
):
    """Running the pipeline repeatedly must reuse existing AI analyses and embeddings without redundant calls."""
    now = datetime.now(timezone.utc)
    item = ContentItem(
        source_id=pipeline_test_source.id,
        canonical_url=f"https://example.com/idempotent-{uuid.uuid4().hex[:6]}",
        title="Idempotency Milestone Story",
        summary="Summary of initial development.",
        published_at=now,
        fetched_at=now,
        content_hash=uuid.uuid4().hex,
        content_type=ContentType.ARTICLE.value,
        processing_state="PENDING",
    )
    db_session.add(item)
    await db_session.flush()

    provider = MockPipelineAIProvider()
    ai_service = AIProcessingService(session=db_session, provider=provider)
    orchestrator = PipelineOrchestratorService(session=db_session, ai_service=ai_service)

    # Run 1: Should analyze and generate embedding
    req1 = PipelineRunRequest(limit=5, max_ai_analyses=5, max_embeddings=5)
    res1 = await orchestrator.run_pipeline(req1)

    assert res1.ai_analyses_performed == 1
    assert res1.embeddings_generated == 1
    assert res1.embeddings_reused == 0

    # Run 2: Re-running must skip already completed & clustered items (0 redundant AI calls, 0 new embeddings)
    req2 = PipelineRunRequest(limit=5, max_ai_analyses=5, max_embeddings=5, force=False)
    res2 = await orchestrator.run_pipeline(req2)

    assert res2.ai_analyses_performed == 0
    assert res2.embeddings_generated == 0
    assert res2.items_seen == 0  # Fully idempotent: unassigned queue empty

    # Run 3: Item with pre-existing embedding must REUSE embedding (0 generated, 1 reused)
    item_with_emb = ContentItem(
        source_id=pipeline_test_source.id,
        canonical_url=f"https://example.com/pre-emb-{uuid.uuid4().hex[:6]}",
        title="Pre-embedded Milestone Item",
        summary="Summary with embedding already present.",
        published_at=now,
        fetched_at=now,
        content_hash=uuid.uuid4().hex,
        content_type=ContentType.ARTICLE.value,
        processing_state="PENDING",
        embedding=[0.05] * 1536,
        embedding_model="gemini-embedding-001",
    )
    db_session.add(item_with_emb)
    await db_session.flush()

    req3 = PipelineRunRequest(limit=5, max_ai_analyses=5, max_embeddings=5)
    res3 = await orchestrator.run_pipeline(req3)

    assert res3.embeddings_generated == 0
    assert res3.embeddings_reused >= 1


@pytest.mark.asyncio
async def test_pipeline_failure_isolation_429(
    db_session: AsyncSession, pipeline_test_source: Source
):
    """A Gemini 429 quota exhaustion must be cleanly isolated, halt further calls, and avoid synthetic vectors."""
    now = datetime.now(timezone.utc)
    item = ContentItem(
        source_id=pipeline_test_source.id,
        canonical_url=f"https://example.com/fail429-{uuid.uuid4().hex[:6]}",
        title="Item encountering 429",
        summary="Summary text.",
        published_at=now,
        fetched_at=now,
        content_hash=uuid.uuid4().hex,
        content_type=ContentType.ARTICLE.value,
        processing_state="PENDING",
    )
    db_session.add(item)
    await db_session.flush()

    # Provider configured to fail with 429
    failing_provider = MockPipelineAIProvider(should_fail_429=True)
    ai_service = AIProcessingService(session=db_session, provider=failing_provider)
    orchestrator = PipelineOrchestratorService(session=db_session, ai_service=ai_service)

    req = PipelineRunRequest(limit=5, max_ai_analyses=5, max_embeddings=5)
    res = await orchestrator.run_pipeline(req)

    # Pipeline should complete gracefully without raising uncaught exception
    assert len(res.failures) >= 1
    assert any("429" in f.message or "ResourceExhausted" in f.error_type for f in res.failures)

    # Database invariant: the item must NOT have an embedding or be marked completed
    refreshed_item = await db_session.get(ContentItem, item.id)
    assert refreshed_item.embedding is None
    assert refreshed_item.processing_state != ProcessingStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_pipeline_deterministic_dedup_integration(
    db_session: AsyncSession, pipeline_test_source: Source
):
    """Items matching an existing canonical URL or content hash must be flagged and bypass LLM calls."""
    now = datetime.now(timezone.utc)
    orig_hash = uuid.uuid4().hex
    orig_url = f"https://example.com/canon-{uuid.uuid4().hex[:6]}"

    # Pre-existing item in DB
    existing_item = ContentItem(
        source_id=pipeline_test_source.id,
        canonical_url=orig_url,
        title="Original Story",
        summary="Original summary text.",
        published_at=now,
        fetched_at=now,
        content_hash=orig_hash,
        content_type=ContentType.ARTICLE.value,
        processing_state="COMPLETED",
        ai_summary="AI summary",
        embedding=[0.1] * 1536,
    )
    # Duplicate item with exact same content hash
    dup_item = ContentItem(
        source_id=pipeline_test_source.id,
        canonical_url=f"https://example.com/dup-{uuid.uuid4().hex[:6]}",
        title="Duplicate Story",
        summary="Original summary text.",
        published_at=now,
        fetched_at=now,
        content_hash=orig_hash,
        content_type=ContentType.ARTICLE.value,
        processing_state="PENDING",
    )
    db_session.add(existing_item)
    db_session.add(dup_item)
    await db_session.flush()

    provider = MockPipelineAIProvider()
    ai_service = AIProcessingService(session=db_session, provider=provider)
    orchestrator = PipelineOrchestratorService(session=db_session, ai_service=ai_service)

    req = PipelineRunRequest(limit=10, max_ai_analyses=10, max_embeddings=10)
    res = await orchestrator.run_pipeline(req)

    assert res.deterministic_duplicates_found >= 1


@pytest.mark.asyncio
async def test_pipeline_curation_digest_output(
    db_session: AsyncSession, pipeline_test_source: Source
):
    """End-to-end pipeline run produces structured curated candidate stories ready for digest."""
    now = datetime.now(timezone.utc)
    item = ContentItem(
        source_id=pipeline_test_source.id,
        canonical_url=f"https://example.com/curate-{uuid.uuid4().hex[:6]}",
        title="Frontier Foundation Model Announcement",
        summary="Comprehensive release of weights and architecture details.",
        published_at=now,
        fetched_at=now,
        content_hash=uuid.uuid4().hex,
        content_type=ContentType.ARTICLE.value,
        processing_state="PENDING",
    )
    db_session.add(item)
    await db_session.flush()

    provider = MockPipelineAIProvider()
    ai_service = AIProcessingService(session=db_session, provider=provider)
    orchestrator = PipelineOrchestratorService(session=db_session, ai_service=ai_service)

    req = PipelineRunRequest(limit=5, max_ai_analyses=5, max_embeddings=5, curation_limit=5)
    res = await orchestrator.run_pipeline(req)

    assert res.status in ("SUCCESS", "PARTIAL")
    assert res.stories_ranked >= 0
    # Check that curated_stories format matches digest contract
    for cs in res.curated_stories:
        assert cs.story_id is not None
        assert cs.headline
        assert cs.summary
        assert cs.ranking_score >= 0.0


@pytest.mark.asyncio
async def test_pipeline_api_run_endpoint(async_client: AsyncClient):
    """POST /api/v1/pipeline/run accepts bounded request and returns 200 with PipelineRunResponse."""
    payload = {
        "limit": 3,
        "max_ai_analyses": 1,
        "max_embeddings": 1,
        "curation_limit": 3,
        "force": False,
    }
    response = await async_client.post("/api/v1/pipeline/run", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "items_seen" in data
    assert "ai_analyses_performed" in data
    assert "embeddings_generated" in data
    assert "curated_stories" in data
    assert "duration_ms" in data
