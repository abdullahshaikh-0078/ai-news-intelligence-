from datetime import datetime, timezone
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import AsyncMock

from app.domain.models.source import SourceType
from app.domain.schemas.ingestion import NormalizedArticle
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.source import Source
from app.infrastructure.repositories.source_repo import SourceRepository
from app.ingestion.adapters.youtube_adapter import YouTubeAdapter
from app.ingestion.canonicalizer import generate_content_hash
from app.ingestion.orchestrator import IngestionOrchestrator
from app.services.source_service import SourceService


@pytest.mark.asyncio
async def test_youtube_source_seeding(db_session: AsyncSession):
    """Verify default seeding registers the baseline YouTube channels."""
    repo = SourceRepository(db_session)
    service = SourceService(repo)

    seeded = await service.seed_default_sources()
    yt_sources = [s for s in seeded if s.type == SourceType.YOUTUBE.value]
    assert len(yt_sources) == 3

    slugs = {s.slug for s in yt_sources}
    assert "two-minute-papers" in slugs
    assert "yannic-kilcher" in slugs
    assert "ai-explained" in slugs

    tmp = next(s for s in yt_sources if s.slug == "two-minute-papers")
    assert tmp.config.get("channel_id") == "UCbfYPyITQ-7l4upoX8nvctg"
    assert tmp.config.get("max_results") == 10


@pytest.mark.asyncio
async def test_youtube_ingestion_and_idempotency(db_session: AsyncSession):
    """Verify full ingestion cycle: 2 inserted on first run, 0 inserted on second run."""
    source_repo = SourceRepository(db_session)
    source = Source(
        name="Two Minute Papers",
        slug="two-minute-papers-test",
        type=SourceType.YOUTUBE.value,
        url="https://www.youtube.com/channel/UCbfYPyITQ-7l4upoX8nvctg",
        enabled=True,
        config={"channel_id": "UCbfYPyITQ-7l4upoX8nvctg"},
    )
    created_source = await source_repo.add(source)

    now = datetime.now(timezone.utc)
    mock_articles = [
        NormalizedArticle(
            source_id=created_source.id,
            canonical_url="https://www.youtube.com/watch?v=vid_test_1",
            title="AI Breakthrough 1",
            summary="Summary 1",
            raw_content="Description 1",
            author="Two Minute Papers",
            published_at=now,
            fetched_at=now,
            external_id="youtube:vid_test_1",
            language="en",
            content_hash=generate_content_hash("AI Breakthrough 1", "Description 1", "Two Minute Papers"),
            metadata_json={"video_id": "vid_test_1", "view_count": 1000, "like_count": 100, "comment_count": 20},
        ),
        NormalizedArticle(
            source_id=created_source.id,
            canonical_url="https://www.youtube.com/watch?v=vid_test_2",
            title="AI Breakthrough 2",
            summary="Summary 2",
            raw_content="Description 2",
            author="Two Minute Papers",
            published_at=now,
            fetched_at=now,
            external_id="youtube:vid_test_2",
            language="en",
            content_hash=generate_content_hash("AI Breakthrough 2", "Description 2", "Two Minute Papers"),
            metadata_json={"video_id": "vid_test_2", "view_count": 2000, "like_count": 200, "comment_count": 40},
        ),
    ]

    orchestrator = IngestionOrchestrator(db_session)
    mock_adapter = AsyncMock(spec=YouTubeAdapter)
    mock_adapter.fetch_and_parse.return_value = mock_articles
    orchestrator.adapters[SourceType.YOUTUBE.value] = mock_adapter

    # Run 1: initial ingestion
    res1 = await orchestrator.ingest_source(created_source)
    assert res1.status == "SUCCESS"
    assert res1.entries_fetched == 2
    assert res1.entries_inserted == 2
    assert res1.entries_skipped == 0
    assert res1.entries_updated == 0

    count_stmt = select(func.count(ContentItem.id)).where(ContentItem.source_id == created_source.id)
    assert (await db_session.execute(count_stmt)).scalar() == 2

    # Run 2: repeat ingestion with identical data must be 100% idempotent
    res2 = await orchestrator.ingest_source(created_source)
    assert res2.status == "SUCCESS"
    assert res2.entries_fetched == 2
    assert res2.entries_inserted == 0
    assert res2.entries_skipped == 2
    assert res2.entries_updated == 0

    assert (await db_session.execute(count_stmt)).scalar() == 2


@pytest.mark.asyncio
async def test_youtube_dynamic_metrics_update(db_session: AsyncSession):
    """Verify that changes in view_count or like_count update existing record in place."""
    source_repo = SourceRepository(db_session)
    source = Source(
        name="Yannic Kilcher",
        slug="yannic-kilcher-metrics-test",
        type=SourceType.YOUTUBE.value,
        url="https://www.youtube.com/channel/UCEBm0xLn28l-H0V586dJ7qw",
        enabled=True,
        config={"channel_id": "UCEBm0xLn28l-H0V586dJ7qw"},
    )
    created_source = await source_repo.add(source)

    now = datetime.now(timezone.utc)
    base_article = NormalizedArticle(
        source_id=created_source.id,
        canonical_url="https://www.youtube.com/watch?v=vid_metrics_1",
        title="DeepSeek R1 Paper Review",
        summary="Summary of DeepSeek R1",
        raw_content="Full description of DeepSeek R1",
        author="Yannic Kilcher",
        published_at=now,
        fetched_at=now,
        external_id="youtube:vid_metrics_1",
        language="en",
        content_hash=generate_content_hash("DeepSeek R1 Paper Review", "Full description of DeepSeek R1", "Yannic Kilcher"),
        metadata_json={"video_id": "vid_metrics_1", "view_count": 50000, "like_count": 4000, "comment_count": 500},
    )

    orchestrator = IngestionOrchestrator(db_session)
    mock_adapter = AsyncMock(spec=YouTubeAdapter)
    mock_adapter.fetch_and_parse.return_value = [base_article]
    orchestrator.adapters[SourceType.YOUTUBE.value] = mock_adapter

    # First run inserts
    res1 = await orchestrator.ingest_source(created_source)
    assert res1.entries_inserted == 1

    # Second run: views and likes surged!
    updated_article = base_article.model_copy(deep=True)
    updated_article.metadata_json = {
        "video_id": "vid_metrics_1",
        "view_count": 125000,  # updated
        "like_count": 9500,    # updated
        "comment_count": 1200, # updated
    }
    mock_adapter.fetch_and_parse.return_value = [updated_article]

    res2 = await orchestrator.ingest_source(created_source)
    assert res2.entries_inserted == 0
    assert res2.entries_updated == 1
    assert res2.entries_skipped == 0

    # Verify updated values in DB
    item_stmt = select(ContentItem).where(ContentItem.source_id == created_source.id)
    persisted_item = (await db_session.execute(item_stmt)).scalar_one()
    assert persisted_item.metadata_json["view_count"] == 125000
    assert persisted_item.metadata_json["like_count"] == 9500
    assert persisted_item.metadata_json["comment_count"] == 1200


@pytest.mark.asyncio
async def test_youtube_fault_isolation(db_session: AsyncSession):
    """Verify that a failure in one YouTube channel does not abort the entire batch."""
    source_repo = SourceRepository(db_session)
    source_failing = await source_repo.add(
        Source(
            name="Failing Channel",
            slug="failing-yt-channel",
            type=SourceType.YOUTUBE.value,
            url="https://www.youtube.com/channel/UCfailing",
            enabled=True,
        )
    )
    source_healthy = await source_repo.add(
        Source(
            name="Healthy Channel",
            slug="healthy-yt-channel",
            type=SourceType.YOUTUBE.value,
            url="https://www.youtube.com/channel/UChealthy",
            enabled=True,
        )
    )

    orchestrator = IngestionOrchestrator(db_session)
    mock_adapter = AsyncMock(spec=YouTubeAdapter)

    now = datetime.now(timezone.utc)
    healthy_article = NormalizedArticle(
        source_id=source_healthy.id,
        canonical_url="https://www.youtube.com/watch?v=vid_healthy",
        title="Healthy Video",
        published_at=now,
        fetched_at=now,
        external_id="youtube:vid_healthy",
        content_hash=generate_content_hash("Healthy Video", "", "Healthy Channel"),
        metadata_json={"video_id": "vid_healthy"},
    )

    async def side_effect(src):
        if src.id == source_failing.id:
            raise RuntimeError("Simulated YouTube quota error or network drop")
        return [healthy_article]

    mock_adapter.fetch_and_parse.side_effect = side_effect
    orchestrator.adapters[SourceType.YOUTUBE.value] = mock_adapter

    summary = await orchestrator.ingest_all_youtube_sources()
    # At least failing and healthy channels were processed
    assert summary.succeeded_sources >= 1
    assert summary.failed_sources >= 1

    failing_res = next(r for r in summary.source_results if r.source_id == source_failing.id)
    assert failing_res.status == "FAILED"
    assert "Simulated YouTube quota error" in failing_res.errors[0]

    healthy_res = next(r for r in summary.source_results if r.source_id == source_healthy.id)
    assert healthy_res.status == "SUCCESS"
    assert healthy_res.entries_inserted == 1
