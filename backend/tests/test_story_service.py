from datetime import datetime, timedelta, timezone
import uuid
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.content_item import ContentType
from app.domain.models.story import StoryStatus
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.source import Source
from app.infrastructure.models.story import Story
from app.infrastructure.models.story_content_item import StoryContentItem
from app.infrastructure.repositories.content_repo import ContentRepository
from app.infrastructure.repositories.source_repo import SourceRepository
from app.infrastructure.repositories.story_repo import StoryRepository
from app.services.story_service import (
    StoryClusteringService,
    clean_story_title,
    select_representative_item,
    synthesize_story_content,
)


def _normalize(vec: list[float]) -> list[float]:
    norm = sum(x * x for x in vec) ** 0.5
    return [x / norm for x in vec] if norm > 0 else vec


def make_vector(val: float = 1.0) -> list[float]:
    vec = [0.0] * 1536
    vec[0] = val
    vec[1] = 0.5
    return _normalize(vec)


def make_similar_vector(base: list[float], offset: float) -> list[float]:
    vec = list(base)
    vec[2] = offset
    return _normalize(vec)


@pytest.fixture
async def story_test_source(db_session: AsyncSession) -> Source:
    source_repo = SourceRepository(db_session)
    source = await source_repo.add(
        Source(
            name="Story Test Source",
            slug=f"story-src-{uuid.uuid4().hex[:8]}",
            type="RSS",
            url=f"https://example.com/story-feed-{uuid.uuid4().hex[:8]}.xml",
            enabled=True,
        )
    )
    return source


def test_clean_story_title():
    """Verify deterministic title cleaning removes noise prefixes and trailing branding."""
    assert clean_story_title("Show HN: SuperAgent AI release") == "SuperAgent AI release"
    assert clean_story_title("Ask HN: How to build agents?") == "How to build agents?"
    assert clean_story_title("[PDF] Foundation Models in 2026 - arXiv") == "Foundation Models in 2026"
    assert clean_story_title("Research: Deep Reasoning Breakthrough - TechCrunch") == "Deep Reasoning Breakthrough"
    assert clean_story_title("Normal Title Unchanged") == "Normal Title Unchanged"


def test_select_representative_item():
    """Verify representative item selection prefers higher signal content types and relevance scores."""
    now = datetime.now(timezone.utc)
    source_id = uuid.uuid4()

    comm_post = ContentItem(
        id=uuid.uuid4(),
        source_id=source_id,
        canonical_url="https://example.com/1",
        title="HN Post on Release",
        content_type=ContentType.COMMUNITY_POST.value,
        ai_relevance_score=0.4,
        published_at=now,
        fetched_at=now,
        content_hash="h1",
    )

    article = ContentItem(
        id=uuid.uuid4(),
        source_id=source_id,
        canonical_url="https://example.com/2",
        title="In-depth Article: The New Architecture",
        content_type=ContentType.ARTICLE.value,
        ai_relevance_score=0.85,
        published_at=now,
        fetched_at=now,
        content_hash="h2",
    )

    paper = ContentItem(
        id=uuid.uuid4(),
        source_id=source_id,
        canonical_url="https://example.com/3",
        title="Academic Paper: Foundation Reasoning Models",
        content_type=ContentType.RESEARCH_PAPER.value,
        ai_relevance_score=0.9,
        published_at=now,
        fetched_at=now,
        content_hash="h3",
    )

    # Paper should be selected over article and community post
    rep = select_representative_item([comm_post, article, paper])
    assert rep.id == paper.id

    # Article selected over community post
    rep2 = select_representative_item([comm_post, article])
    assert rep2.id == article.id


def test_synthesize_story_content():
    """Verify grounded multi-source synthesis compiles summaries and key takeaways."""
    now = datetime.now(timezone.utc)
    source_id = uuid.uuid4()

    item1 = ContentItem(
        id=uuid.uuid4(),
        source_id=source_id,
        canonical_url="https://example.com/1",
        title="Item 1 Title",
        content_type=ContentType.ARTICLE.value,
        ai_summary="OpenAI released a major multimodal model with breakthrough reasoning.",
        ai_key_points=["Processes multimodal tokens natively.", "Latency reduced by 40%."],
        ai_topics=["Multimodal AI", "LLMs"],
        published_at=now,
        fetched_at=now,
        content_hash="h1",
    )

    item2 = ContentItem(
        id=uuid.uuid4(),
        source_id=source_id,
        canonical_url="https://example.com/2",
        title="Item 2 Title",
        content_type=ContentType.ARTICLE.value,
        ai_summary="Independent benchmarks verify 40% latency reduction across complex workloads.",
        ai_key_points=["Benchmarks show superior reasoning."],
        ai_topics=["Benchmarking"],
        published_at=now,
        fetched_at=now,
        content_hash="h2",
    )

    summary, takeaway, why_it_matters = synthesize_story_content([item1, item2])
    assert "OpenAI released a major multimodal model" in summary
    assert "Multi-source reporting confirms developments" in summary
    assert "Processes multimodal tokens natively." in takeaway
    assert "Multimodal AI" in why_it_matters or "LLMs" in why_it_matters


@pytest.mark.asyncio
async def test_story_creation_and_association(
    db_session: AsyncSession,
    story_test_source: Source,
):
    """Verify Story creation with StoryContentItem association and canonical item designation."""
    content_repo = ContentRepository(db_session)
    story_repo = StoryRepository(db_session)
    now = datetime.now(timezone.utc)

    item = await content_repo.add(
        ContentItem(
            source_id=story_test_source.id,
            canonical_url=f"https://example.com/item-{uuid.uuid4().hex[:6]}",
            title="Reasoning Engine 2.0 Launched",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            embedding=make_vector(1.0),
        )
    )

    story = await story_repo.create_story(
        title="Reasoning Engine 2.0 Launched",
        summary="Summary of release.",
        canonical_item_id=item.id,
        status="ACTIVE",
    )
    assoc = await story_repo.add_item_to_story(
        story_id=story.id,
        content_item_id=item.id,
        is_canonical=True,
    )

    assert story.id is not None
    assert story.title == "Reasoning Engine 2.0 Launched"
    assert assoc.story_id == story.id
    assert assoc.content_item_id == item.id
    assert assoc.is_canonical is True

    # Legacy pointer is also set
    item_loaded = await content_repo.get_by_id(item.id)
    assert item_loaded.story_id == story.id


@pytest.mark.asyncio
async def test_multi_signal_clustering_scenarios(
    db_session: AsyncSession,
    story_test_source: Source,
):
    """
    Test clustering decisions across multiple signals:
    - High similarity within window -> grouped into same story.
    - High similarity OUTSIDE window -> separate stories.
    - Low similarity -> separate stories.
    """
    content_repo = ContentRepository(db_session)
    now = datetime.now(timezone.utc)
    old_time = now - timedelta(days=10)  # Outside 72h window

    base_vec = make_vector(1.0)
    near_vec = make_similar_vector(base_vec, 0.05)  # Sim ~ 0.99
    distinct_vec = make_similar_vector(base_vec, 3.0)  # Low sim

    # Item 1: Announcement (now)
    item_announcement = await content_repo.add(
        ContentItem(
            source_id=story_test_source.id,
            canonical_url=f"https://example.com/event-1-{uuid.uuid4().hex[:6]}",
            title="OpenAI Announces GPT-5",
            ai_summary="Official launch announcement of GPT-5.",
            ai_topics=["LLMs", "AI Announcements"],
            ai_relevance_score=0.9,
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            embedding=base_vec,
        )
    )

    # Item 2: Analysis within window (now + 2 hours) -> Same Story
    item_analysis = await content_repo.add(
        ContentItem(
            source_id=story_test_source.id,
            canonical_url=f"https://example.com/event-2-{uuid.uuid4().hex[:6]}",
            title="Analysis: Inside OpenAI's GPT-5",
            ai_summary="Technical breakdown of GPT-5 parameters.",
            ai_topics=["LLMs"],
            ai_relevance_score=0.85,
            published_at=now + timedelta(hours=2),
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            embedding=near_vec,
        )
    )

    # Item 3: Similar topic but 10 days ago -> Should NOT join Item 1's story
    item_past = await content_repo.add(
        ContentItem(
            source_id=story_test_source.id,
            canonical_url=f"https://example.com/event-3-{uuid.uuid4().hex[:6]}",
            title="Rumor: GPT-5 Expected Soon",
            ai_summary="Early leaks regarding GPT-5.",
            ai_topics=["LLMs"],
            ai_relevance_score=0.6,
            published_at=old_time,
            fetched_at=old_time,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            embedding=near_vec,
        )
    )

    # Item 4: Completely distinct topic -> Separate Story
    item_robotics = await content_repo.add(
        ContentItem(
            source_id=story_test_source.id,
            canonical_url=f"https://example.com/event-4-{uuid.uuid4().hex[:6]}",
            title="Boston Dynamics Atlas Robot Updates",
            ai_summary="Hydraulic to electric transition updates.",
            ai_topics=["Robotics"],
            ai_relevance_score=0.7,
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            embedding=distinct_vec,
        )
    )

    service = StoryClusteringService(
        session=db_session,
        similarity_threshold=0.85,
        window_hours=72,
    )
    cluster_res = await service.cluster_unassigned_candidates(limit=50)

    assert cluster_res.candidates_scanned >= 4
    assert cluster_res.stories_created >= 2

    # Reload items to inspect story associations
    a1 = await content_repo.get_by_id(item_announcement.id)
    a2 = await content_repo.get_by_id(item_analysis.id)
    a3 = await content_repo.get_by_id(item_past.id)
    a4 = await content_repo.get_by_id(item_robotics.id)

    # Item 1 and Item 2 must be in the same story
    assert a1.story_id is not None
    assert a2.story_id is not None
    assert a1.story_id == a2.story_id

    # Item 4 (robotics) must NOT be in the same story as Item 1
    assert a4.story_id is not None
    assert a4.story_id != a1.story_id

    # Item 3 (from 10 days ago) must NOT be in Item 1's story (outside 72h window)
    if a3.story_id is not None:
        assert a3.story_id != a1.story_id


@pytest.mark.asyncio
async def test_missing_embedding_handling_safe(
    db_session: AsyncSession,
    story_test_source: Source,
):
    """Verify that ContentItems lacking embeddings are safely skipped without external calls."""
    content_repo = ContentRepository(db_session)
    now = datetime.now(timezone.utc)

    no_emb = await content_repo.add(
        ContentItem(
            source_id=story_test_source.id,
            canonical_url=f"https://example.com/no-emb-{uuid.uuid4().hex[:6]}",
            title="Unembedded Raw Article",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            embedding=None,
        )
    )

    service = StoryClusteringService(session=db_session)
    res = await service.cluster_unassigned_candidates(limit=10)

    # Item should remain unclustered safely
    reloaded = await content_repo.get_by_id(no_emb.id)
    assert reloaded.story_id is None


@pytest.mark.asyncio
async def test_non_destructive_invariant_on_clustering(
    db_session: AsyncSession,
    story_test_source: Source,
):
    """Verify that clustering never deletes, merges, or alters ContentItem source data."""
    content_repo = ContentRepository(db_session)
    now = datetime.now(timezone.utc)

    count_before = (await db_session.execute(select(func.count()).select_from(ContentItem))).scalar_one()

    v1 = make_vector(1.0)
    v2 = make_similar_vector(v1, 0.04)

    item1 = await content_repo.add(
        ContentItem(
            source_id=story_test_source.id,
            canonical_url=f"https://example.com/inv-1-{uuid.uuid4().hex[:6]}",
            title="Invariant Test Item 1",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            embedding=v1,
        )
    )

    item2 = await content_repo.add(
        ContentItem(
            source_id=story_test_source.id,
            canonical_url=f"https://example.com/inv-2-{uuid.uuid4().hex[:6]}",
            title="Invariant Test Item 2",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            embedding=v2,
        )
    )

    service = StoryClusteringService(session=db_session)
    await service.cluster_unassigned_candidates(limit=10)

    # Count must reflect only the newly added items, exactly 0 deletions
    count_after = (await db_session.execute(select(func.count()).select_from(ContentItem))).scalar_one()
    assert count_after == count_before + 2

    # Both items must remain intact
    i1 = await content_repo.get_by_id(item1.id)
    i2 = await content_repo.get_by_id(item2.id)
    assert i1 is not None and i2 is not None
    assert i1.canonical_url == item1.canonical_url
    assert i2.canonical_url == item2.canonical_url
    assert len(i1.embedding) == 1536
    assert len(i2.embedding) == 1536
