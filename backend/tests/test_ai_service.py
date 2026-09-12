from datetime import datetime, timezone
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.mock_provider import MockAIProvider
from app.domain.models.content_item import ContentType, ProcessingStatus
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.source import Source
from app.infrastructure.repositories.content_repo import ContentRepository
from app.infrastructure.repositories.source_repo import SourceRepository
from app.services.ai_service import AIProcessingService


@pytest.fixture
async def test_source(db_session: AsyncSession) -> Source:
    source_repo = SourceRepository(db_session)
    source = await source_repo.add(
        Source(
            name="AI Service Test Source",
            slug=f"ai-test-src-{uuid.uuid4().hex[:8]}",
            type="RSS",
            url=f"https://example.com/feed-{uuid.uuid4().hex[:8]}.xml",
            enabled=True,
        )
    )
    return source


def test_compile_processing_text_across_content_types():
    """Verify canonical text compiler extracts bounded, well-formatted text across all content types."""
    dummy_source_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    service = AIProcessingService(session=None, provider=MockAIProvider())

    # 1. Article
    article = ContentItem(
        source_id=dummy_source_id,
        canonical_url="https://example.com/art1",
        title="Frontier Model Release",
        summary="A brief summary of the release.",
        raw_content="Extended article body text detailing the release architecture and benchmarks.",
        author="Research Team",
        published_at=now,
        fetched_at=now,
        content_hash="hash1",
        content_type=ContentType.ARTICLE.value,
    )
    text_art = service.compile_processing_text(article)
    assert "Title: Frontier Model Release" in text_art
    assert "Author/Source: Research Team" in text_art
    assert "Summary/Abstract: A brief summary of the release." in text_art
    assert "Extended article body text" in text_art

    # 2. ArXiv Research Paper
    paper = ContentItem(
        source_id=dummy_source_id,
        canonical_url="https://arxiv.org/abs/2601.99999",
        title="Reasoning Scaling Laws in Diffusion Transformers",
        summary="Abstract of scaling laws.",
        raw_content="Abstract of scaling laws.",
        author="Dr. Alice, Dr. Bob",
        published_at=now,
        fetched_at=now,
        content_hash="hash2",
        content_type=ContentType.RESEARCH_PAPER.value,
        metadata_json={"categories": ["cs.AI", "cs.LG"]},
    )
    text_paper = service.compile_processing_text(paper)
    assert "Title: Reasoning Scaling Laws" in text_paper
    assert "Categories: cs.AI, cs.LG" in text_paper

    # 3. YouTube Video (NO transcript extraction)
    video = ContentItem(
        source_id=dummy_source_id,
        canonical_url="https://www.youtube.com/watch?v=vid123",
        title="Claude 3.7 Architecture Deep Dive",
        summary="Video breakdown description.",
        author="AI Explained",
        published_at=now,
        fetched_at=now,
        content_hash="hash3",
        content_type=ContentType.VIDEO.value,
    )
    text_video = service.compile_processing_text(video)
    assert "Title: Claude 3.7 Architecture Deep Dive" in text_video
    assert "Video breakdown description" in text_video

    # 4. Hacker News Community Post
    post = ContentItem(
        source_id=dummy_source_id,
        canonical_url="https://news.ycombinator.com/item?id=888888",
        title="Show HN: Local LLM Orchestrator",
        summary="Discussion thread on local models.",
        author="hacker",
        published_at=now,
        fetched_at=now,
        content_hash="hash4",
        content_type=ContentType.COMMUNITY_POST.value,
        metadata_json={"score": 150, "comments": 42},
    )
    text_post = service.compile_processing_text(post)
    assert "Community Engagement: 150 points, 42 comments" in text_post


@pytest.mark.asyncio
async def test_process_item_success_and_persistence(db_session: AsyncSession, test_source: Source):
    """Verify single ContentItem processing populates AI fields, embedding, and COMPLETED state."""
    content_repo = ContentRepository(db_session)
    now = datetime.now(timezone.utc)

    item = await content_repo.add(
        ContentItem(
            source_id=test_source.id,
            canonical_url=f"https://example.com/item-{uuid.uuid4().hex[:6]}",
            title="Next-Generation Reasoning LLMs",
            summary="Discussion on reinforcement learning applied to reasoning models.",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            processing_state="PENDING",
        )
    )

    mock_provider = MockAIProvider(chat_model="test-gpt", embedding_model="test-emb")
    service = AIProcessingService(session=db_session, provider=mock_provider)

    processed = await service.process_item(item)
    assert processed.processing_state == ProcessingStatus.COMPLETED.value
    assert processed.ai_summary is not None
    assert len(processed.ai_key_points) >= 2
    assert len(processed.ai_topics) >= 1
    assert 0.0 <= processed.ai_relevance_score <= 1.0
    assert processed.ai_model == "test-gpt"
    assert processed.embedding_model == "test-emb"
    assert processed.embedding is not None
    assert len(processed.embedding) == 1536
    assert processed.ai_processed_at is not None

    # Reload from DB and verify persistence
    reloaded = await content_repo.get_by_id(item.id)
    assert reloaded is not None
    assert reloaded.processing_state == ProcessingStatus.COMPLETED.value
    assert reloaded.ai_summary == processed.ai_summary
    assert len(reloaded.embedding) == 1536


@pytest.mark.asyncio
async def test_process_item_idempotency(db_session: AsyncSession, test_source: Source):
    """Verify that completed items are skipped on repeat runs unless force=True."""
    content_repo = ContentRepository(db_session)
    now = datetime.now(timezone.utc)

    item = await content_repo.add(
        ContentItem(
            source_id=test_source.id,
            canonical_url=f"https://example.com/idempotency-{uuid.uuid4().hex[:6]}",
            title="Idempotency Test Article",
            summary="Original summary text.",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            processing_state="PENDING",
        )
    )

    mock_provider = MockAIProvider()
    service = AIProcessingService(session=db_session, provider=mock_provider)

    # 1. Initial run
    await service.process_item(item)
    assert mock_provider.call_count_analyze == 1
    assert mock_provider.call_count_embedding == 1

    # 2. Second run without force -> skipped
    await service.process_item(item, force=False)
    assert mock_provider.call_count_analyze == 1  # Not incremented
    assert mock_provider.call_count_embedding == 1

    # 3. Third run with force=True -> reprocessed
    await service.process_item(item, force=True)
    assert mock_provider.call_count_analyze == 2
    assert mock_provider.call_count_embedding == 2


@pytest.mark.asyncio
async def test_process_item_fault_isolation(db_session: AsyncSession, test_source: Source):
    """Verify that provider failure marks item as FAILED and preserves original source fields."""
    content_repo = ContentRepository(db_session)
    now = datetime.now(timezone.utc)

    item = await content_repo.add(
        ContentItem(
            source_id=test_source.id,
            canonical_url=f"https://example.com/fail-{uuid.uuid4().hex[:6]}",
            title="Original Untouched Title",
            summary="Original Pristine Summary",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            processing_state="PENDING",
        )
    )

    failing_provider = MockAIProvider(simulate_error=RuntimeError("Provider offline"))
    service = AIProcessingService(session=db_session, provider=failing_provider)

    with pytest.raises(RuntimeError, match="Provider offline"):
        await service.process_item(item)

    # Reload from DB and verify preservation
    reloaded = await content_repo.get_by_id(item.id)
    assert reloaded is not None
    assert reloaded.processing_state == ProcessingStatus.FAILED.value
    assert reloaded.title == "Original Untouched Title"
    assert reloaded.summary == "Original Pristine Summary"
    assert reloaded.ai_summary is None


@pytest.mark.asyncio
async def test_process_batch_bounded_concurrency(db_session: AsyncSession, test_source: Source):
    """Verify batch processing tracks total, completed, skipped, and failed items accurately."""
    content_repo = ContentRepository(db_session)
    now = datetime.now(timezone.utc)

    # Create 3 items: 2 pending, 1 already completed
    item1 = await content_repo.add(
        ContentItem(
            source_id=test_source.id,
            canonical_url=f"https://example.com/batch-1-{uuid.uuid4().hex[:6]}",
            title="Batch Item 1",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            processing_state="PENDING",
        )
    )
    item2 = await content_repo.add(
        ContentItem(
            source_id=test_source.id,
            canonical_url=f"https://example.com/batch-2-{uuid.uuid4().hex[:6]}",
            title="Batch Item 2",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            processing_state="PENDING",
        )
    )
    item3 = await content_repo.add(
        ContentItem(
            source_id=test_source.id,
            canonical_url=f"https://example.com/batch-3-{uuid.uuid4().hex[:6]}",
            title="Batch Item 3 (Already Done)",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            processing_state="COMPLETED",
        )
    )

    mock_provider = MockAIProvider()
    service = AIProcessingService(session=db_session, provider=mock_provider, concurrency=2)

    res = await service.process_batch([item1, item2, item3], force=False)
    assert res.total_candidates == 3
    assert res.completed == 2
    assert res.skipped == 1
    assert res.failed == 0
    assert len(res.errors) == 0
