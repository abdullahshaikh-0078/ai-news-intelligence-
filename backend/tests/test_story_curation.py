from datetime import datetime, timezone
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.content_item import ContentType
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.source import Source
from app.infrastructure.models.story import Story
from app.infrastructure.repositories.source_repo import SourceRepository
from app.services.curation_service import StoryCurationService


@pytest.fixture
async def curation_test_sources(db_session: AsyncSession) -> tuple[Source, Source]:
    source_repo = SourceRepository(db_session)
    source_a = await source_repo.add(
        Source(
            name="Curation Source Alpha",
            slug=f"cur-alpha-{uuid.uuid4().hex[:8]}",
            type="RSS",
            url=f"https://example.com/alpha-{uuid.uuid4().hex[:8]}.xml",
            enabled=True,
        )
    )
    source_b = await source_repo.add(
        Source(
            name="Curation Source Beta",
            slug=f"cur-beta-{uuid.uuid4().hex[:8]}",
            type="LABS",
            url=f"https://example.com/beta-{uuid.uuid4().hex[:8]}.xml",
            enabled=True,
        )
    )
    return source_a, source_b


@pytest.mark.asyncio
async def test_curation_minimum_score_and_archived_exclusion(
    db_session: AsyncSession, curation_test_sources: tuple[Source, Source]
):
    """Stories below min_score or marked ARCHIVED must be excluded from curation."""
    source_a, _ = curation_test_sources
    service = StoryCurationService(session=db_session, min_score=0.50)
    now = datetime.now(timezone.utc)

    item1 = ContentItem(
        source_id=source_a.id,
        canonical_url=f"https://example.com/cur-1-{uuid.uuid4().hex[:6]}",
        title="High scoring story",
        published_at=now,
        fetched_at=now,
        content_hash=uuid.uuid4().hex,
        content_type=ContentType.ARTICLE.value,
    )
    db_session.add(item1)
    await db_session.flush()

    # Story 1: Score 0.85 -> Eligible
    story1 = Story(
        headline="High quality breakthrough",
        status="ACTIVE",
        ranking_score=0.85,
        canonical_content_item_id=item1.id,
        category="LLM",
        published_at=now,
    )
    # Story 2: Score 0.30 -> Ineligible (below cutoff 0.50)
    story2 = Story(
        headline="Low quality snippet",
        status="ACTIVE",
        ranking_score=0.30,
        canonical_content_item_id=item1.id,
        category="LLM",
        published_at=now,
    )
    # Story 3: Score 0.90 but ARCHIVED -> Ineligible
    story3 = Story(
        headline="Archived historic event",
        status="ARCHIVED",
        ranking_score=0.90,
        canonical_content_item_id=item1.id,
        category="LLM",
        published_at=now,
    )
    db_session.add_all([story1, story2, story3])
    await db_session.flush()

    telemetry = await service.curate_feed(limit=10, min_score=0.50)
    assert telemetry.curated_count == 1
    assert telemetry.rejected_reasons.get("below_min_score", 0) >= 1
    assert story1.is_curated is True
    assert story2.is_curated is False
    assert story3.is_curated is False


@pytest.mark.asyncio
async def test_curation_topic_and_source_diversity_caps(
    db_session: AsyncSession, curation_test_sources: tuple[Source, Source]
):
    """Verify that curation enforces topic and source diversity limits."""
    source_a, source_b = curation_test_sources
    now = datetime.now(timezone.utc)

    # Create 4 stories:
    # 3 on topic "Robotics" from source_a (scores: 0.90, 0.85, 0.80)
    # 1 on topic "Bioinformatics" from source_b (score: 0.75)
    # With max_per_topic=2, the third Robotics story should be capped, and Bioinformatics should be curated!
    service = StoryCurationService(
        session=db_session,
        min_score=0.50,
        max_per_topic=2,
        max_per_source=3,
        diversity_enabled=True,
    )

    items = []
    for i in range(4):
        src = source_a if i < 3 else source_b
        it = ContentItem(
            source_id=src.id,
            canonical_url=f"https://example.com/div-{i}-{uuid.uuid4().hex[:6]}",
            title=f"Item {i}",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
        )
        items.append(it)
    db_session.add_all(items)
    await db_session.flush()

    s1 = Story(
        headline="Robotics breakthrough 1",
        status="ACTIVE",
        ranking_score=0.90,
        category="Robotics",
        canonical_content_item_id=items[0].id,
        published_at=now,
    )
    s2 = Story(
        headline="Robotics breakthrough 2",
        status="ACTIVE",
        ranking_score=0.85,
        category="Robotics",
        canonical_content_item_id=items[1].id,
        published_at=now,
    )
    s3 = Story(
        headline="Robotics breakthrough 3",
        status="ACTIVE",
        ranking_score=0.80,
        category="Robotics",
        canonical_content_item_id=items[2].id,
        published_at=now,
    )
    s4 = Story(
        headline="Bioinformatics breakthrough",
        status="ACTIVE",
        ranking_score=0.75,
        category="Bioinformatics",
        canonical_content_item_id=items[3].id,
        published_at=now,
    )
    db_session.add_all([s1, s2, s3, s4])
    await db_session.flush()

    telemetry = await service.curate_feed(limit=3, max_per_topic=2)

    assert telemetry.curated_count == 3
    assert telemetry.rejected_reasons.get("topic_cap_reached") == 1
    # s1 and s2 should be curated
    assert s1.is_curated is True
    assert s2.is_curated is True
    # s3 capped due to Robotics limit of 2
    assert s3.is_curated is False
    # s4 curated due to available slot for Bioinformatics
    assert s4.is_curated is True


@pytest.mark.asyncio
async def test_curation_list_curated_feed(
    db_session: AsyncSession, curation_test_sources: tuple[Source, Source]
):
    """Test list_curated_stories returns only is_curated stories ordered by ranking_score."""
    source_a, _ = curation_test_sources
    service = StoryCurationService(session=db_session)
    now = datetime.now(timezone.utc)

    item = ContentItem(
        source_id=source_a.id,
        canonical_url=f"https://example.com/feed-{uuid.uuid4().hex[:6]}",
        title="Feed Item",
        published_at=now,
        fetched_at=now,
        content_hash=uuid.uuid4().hex,
        content_type=ContentType.ARTICLE.value,
    )
    item.source = source_a
    db_session.add(item)
    await db_session.flush()

    s_cur = Story(
        headline="Curated Feed Headline",
        status="ACTIVE",
        is_curated=True,
        ranking_score=0.88,
        canonical_content_item_id=item.id,
        canonical_content_item=item,
        curated_at=now,
        published_at=now,
    )
    s_uncur = Story(
        headline="Uncurated Headline",
        status="ACTIVE",
        is_curated=False,
        ranking_score=0.45,
        canonical_content_item_id=item.id,
        canonical_content_item=item,
        published_at=now,
    )
    db_session.add_all([s_cur, s_uncur])
    await db_session.flush()

    feed = await service.list_curated_stories(limit=10)
    story_ids = [str(f.story_id) for f in feed]
    assert str(s_cur.id) in story_ids
    assert str(s_uncur.id) not in story_ids
