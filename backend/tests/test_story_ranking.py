from datetime import datetime, timezone, timedelta
import math
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.content_item import ContentType
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.source import Source
from app.infrastructure.models.story import Story
from app.infrastructure.models.story_content_item import StoryContentItem
from app.infrastructure.repositories.content_repo import ContentRepository
from app.infrastructure.repositories.source_repo import SourceRepository
from app.services.ranking_service import StoryRankingService


@pytest.fixture
async def ranking_test_source(db_session: AsyncSession) -> Source:
    source_repo = SourceRepository(db_session)
    source = await source_repo.add(
        Source(
            name="Ranking Source A",
            slug=f"rank-src-{uuid.uuid4().hex[:8]}",
            type="RSS",
            url=f"https://example.com/rss-{uuid.uuid4().hex[:8]}.xml",
            reliability_score=0.90,
            enabled=True,
        )
    )
    return source


@pytest.mark.asyncio
async def test_ranking_relevance_calculation(
    db_session: AsyncSession, ranking_test_source: Source
):
    """Test AI relevance normalization and fallback behavior."""
    service = StoryRankingService(session=db_session)
    now = datetime.now(timezone.utc)

    # 1. Canonical item with AI relevance score 0.85
    item = ContentItem(
        source_id=ranking_test_source.id,
        canonical_url=f"https://example.com/rel-1-{uuid.uuid4().hex[:6]}",
        title="Novel Reasoning Architecture",
        published_at=now,
        fetched_at=now,
        content_hash=uuid.uuid4().hex,
        content_type=ContentType.ARTICLE.value,
        ai_relevance_score=0.85,
    )
    db_session.add(item)
    await db_session.flush()

    story = Story(
        headline="Novel Reasoning Architecture",
        status="ACTIVE",
        canonical_content_item_id=item.id,
        canonical_content_item=item,
        published_at=now,
    )

    raw, norm, details = service.calculate_relevance(story, [item])
    assert raw == 0.85
    assert norm == 0.85
    assert details["source_used"] == "canonical_ai_relevance_score"


@pytest.mark.asyncio
async def test_ranking_authority_calculation(
    db_session: AsyncSession, ranking_test_source: Source
):
    """Test source authority based on distinct source reliability."""
    source_repo = SourceRepository(db_session)
    source_b = await source_repo.add(
        Source(
            name="Ranking Source B",
            slug=f"rank-src-b-{uuid.uuid4().hex[:8]}",
            type="LABS",
            url=f"https://example.com/src-b-{uuid.uuid4().hex[:8]}.xml",
            reliability_score=0.70,
            enabled=True,
        )
    )
    service = StoryRankingService(session=db_session)
    now = datetime.now(timezone.utc)

    # Two items from two different sources (0.90 and 0.70 -> avg 0.80)
    item1 = ContentItem(
        source_id=ranking_test_source.id,
        canonical_url=f"https://example.com/auth-1-{uuid.uuid4().hex[:6]}",
        title="Item 1",
        published_at=now,
        fetched_at=now,
        content_hash=uuid.uuid4().hex,
        content_type=ContentType.ARTICLE.value,
    )
    item1.source = ranking_test_source

    item2 = ContentItem(
        source_id=source_b.id,
        canonical_url=f"https://example.com/auth-2-{uuid.uuid4().hex[:6]}",
        title="Item 2",
        published_at=now,
        fetched_at=now,
        content_hash=uuid.uuid4().hex,
        content_type=ContentType.ARTICLE.value,
    )
    item2.source = source_b

    # A third item from ranking_test_source (should be deduplicated by source_id)
    item3 = ContentItem(
        source_id=ranking_test_source.id,
        canonical_url=f"https://example.com/auth-3-{uuid.uuid4().hex[:6]}",
        title="Item 3",
        published_at=now,
        fetched_at=now,
        content_hash=uuid.uuid4().hex,
        content_type=ContentType.ARTICLE.value,
    )
    item3.source = ranking_test_source

    story = Story(headline="Multi Source Story", published_at=now)
    raw, norm, details = service.calculate_authority(story, [item1, item2, item3])

    assert details["distinct_sources_count"] == 2
    assert math.isclose(raw, 0.80, rel_tol=1e-3)
    assert math.isclose(norm, 0.80, rel_tol=1e-3)


def test_ranking_recency_decay():
    """Verify exponential half-life decay function (48h half life)."""
    service = StoryRankingService(session=None, recency_half_life_hours=48.0)
    now = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)

    # 1. Exactly current (0 hours old) -> 1.0
    story_now = Story(headline="Breaking News", published_at=now)
    _, norm_0, _ = service.calculate_recency(story_now, reference_time=now)
    assert norm_0 == 1.0

    # 2. Exactly 48 hours old -> 0.50
    story_48h = Story(headline="Two days old", published_at=now - timedelta(hours=48))
    _, norm_48, _ = service.calculate_recency(story_48h, reference_time=now)
    assert math.isclose(norm_48, 0.50, rel_tol=1e-3)

    # 3. Exactly 96 hours old -> 0.25
    story_96h = Story(headline="Four days old", published_at=now - timedelta(hours=96))
    _, norm_96, _ = service.calculate_recency(story_96h, reference_time=now)
    assert math.isclose(norm_96, 0.25, rel_tol=1e-3)


def test_ranking_coverage_and_diversity():
    """Verify independent coverage scaling and content-type diversity bonus."""
    service = StoryRankingService(session=None)
    src1 = uuid.uuid4()
    src2 = uuid.uuid4()
    src3 = uuid.uuid4()
    src4 = uuid.uuid4()

    # 1 item -> 1 source, 1 type
    item1 = ContentItem(source_id=src1, content_type=ContentType.ARTICLE.value)
    _, cov_1, _ = service.calculate_coverage([item1])
    _, div_1, _ = service.calculate_diversity([item1])
    assert math.isclose(cov_1, math.log(2) / math.log(5), rel_tol=1e-3)  # ~0.43
    assert div_1 == 0.0

    # 4 distinct sources and 3 distinct types
    item2 = ContentItem(source_id=src2, content_type=ContentType.RESEARCH_PAPER.value)
    item3 = ContentItem(source_id=src3, content_type=ContentType.COMMUNITY_POST.value)
    item4 = ContentItem(source_id=src4, content_type=ContentType.ARTICLE.value)

    items = [item1, item2, item3, item4]
    _, cov_4, _ = service.calculate_coverage(items)
    _, div_3, _ = service.calculate_diversity(items)

    assert cov_4 == 1.0  # Saturation target 4
    assert math.isclose(div_3, (3 - 1) / 3.0, rel_tol=1e-3)  # 2/3 = ~0.6667


def test_ranking_engagement_normalization():
    """Verify community engagement extraction and soft-log normalization."""
    service = StoryRankingService(session=None)

    # 1. No engagement metadata
    item_empty = ContentItem(metadata_json={})
    raw_0, norm_0, _ = service.calculate_engagement([item_empty])
    assert raw_0 == 0.0
    assert norm_0 == 0.0

    # 2. Hacker news points and comments
    # raw = 100 points + (2 * 50 comments) = 200
    item_hn = ContentItem(metadata_json={"points": 100, "comments": 50})
    raw_hn, norm_hn, _ = service.calculate_engagement([item_hn])
    assert raw_hn == 200.0
    expected = math.log(201.0) / math.log(501.0)
    assert math.isclose(norm_hn, expected, rel_tol=1e-3)
    assert 0.0 <= norm_hn <= 1.0


@pytest.mark.asyncio
async def test_ranking_end_to_end_evaluation(
    db_session: AsyncSession, ranking_test_source: Source
):
    """Verify overall weighted ranking score bounds [0.0, 1.0] and explanation metadata."""
    service = StoryRankingService(session=db_session)
    now = datetime.now(timezone.utc)

    item = ContentItem(
        source_id=ranking_test_source.id,
        canonical_url=f"https://example.com/eval-{uuid.uuid4().hex[:6]}",
        title="Frontier Multi-Agent Orchestration",
        published_at=now,
        fetched_at=now,
        content_hash=uuid.uuid4().hex,
        content_type=ContentType.ARTICLE.value,
        ai_relevance_score=0.92,
        metadata_json={"points": 150, "comments": 40},
    )
    item.source = ranking_test_source
    db_session.add(item)
    await db_session.flush()

    story = Story(
        headline="Frontier Multi-Agent Orchestration",
        status="ACTIVE",
        canonical_content_item_id=item.id,
        published_at=now,
    )
    db_session.add(story)
    await db_session.flush()

    assoc = StoryContentItem(story_id=story.id, content_item_id=item.id, is_canonical=True)
    db_session.add(assoc)
    await db_session.flush()

    explanation = await service.rank_story(story.id, reference_time=now)

    assert 0.0 <= explanation.total_score <= 1.0
    assert story.ranking_score == explanation.total_score
    assert story.ranking_updated_at is not None
    assert "relevance" in explanation.signals
    assert "authority" in explanation.signals
    assert "recency" in explanation.signals
    assert "coverage" in explanation.signals
    assert "diversity" in explanation.signals
    assert "engagement" in explanation.signals
    assert explanation.signals["relevance"].normalized_value == 0.92
    assert explanation.signals["recency"].normalized_value == 1.0
