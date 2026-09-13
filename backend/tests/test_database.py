from datetime import datetime, timezone
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.infrastructure.database.base import Base
from app.infrastructure.database.session import ping_database
from app.infrastructure.models.source import Source
from app.infrastructure.models.story import Story
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.repositories.source_repo import SourceRepository
from app.infrastructure.repositories.content_repo import ContentRepository
from app.infrastructure.repositories.story_repo import StoryRepository


@pytest.mark.asyncio
async def test_database_connection_and_ping(db_session: AsyncSession):
    """Test raw database ping and session connection."""
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar() == 1

    is_ok, err = await ping_database(db_session)
    assert is_ok is True
    assert err is None


@pytest.mark.asyncio
async def test_model_metadata_registration():
    """Verify that all core models are correctly registered in the SQLAlchemy metadata."""
    registered_tables = Base.metadata.tables.keys()
    assert "sources" in registered_tables
    assert "stories" in registered_tables
    assert "content_items" in registered_tables


@pytest.mark.asyncio
async def test_source_repository_crud(db_session: AsyncSession):
    """Test Source creation, slug lookup, and enabled filtering via SourceRepository."""
    repo = SourceRepository(db_session)
    test_slug = f"arxiv-cs-ai-{uuid.uuid4().hex[:6]}"

    source = Source(
        name="ArXiv cs.AI",
        slug=test_slug,
        type="ARXIV",
        url="http://export.arxiv.org/api/query",
        enabled=True,
        reliability_score=0.95,
        fetch_interval_minutes=120,
        config={"categories": ["cs.AI", "cs.CL"]},
    )
    await repo.add(source)
    await db_session.commit()

    fetched = await repo.get_by_slug(test_slug)
    assert fetched is not None
    assert fetched.name == "ArXiv cs.AI"
    assert fetched.type == "ARXIV"
    assert fetched.config["categories"] == ["cs.AI", "cs.CL"]

    enabled_sources = await repo.list_enabled()
    assert any(s.slug == test_slug for s in enabled_sources)

    arxiv_sources = await repo.list_by_type("ARXIV")
    assert len(arxiv_sources) >= 1


@pytest.mark.asyncio
async def test_content_and_story_provenance_lifecycle(db_session: AsyncSession):
    """Test Story and ContentItem insertion with relational foreign key provenance linking."""
    source_repo = SourceRepository(db_session)
    story_repo = StoryRepository(db_session)
    content_repo = ContentRepository(db_session)

    # 1. Create Source
    source = Source(
        name="OpenAI Research News",
        slug=f"openai-{uuid.uuid4().hex[:6]}",
        type="LABS",
        url="https://openai.com/news/rss.xml",
    )
    await source_repo.add(source)
    await db_session.commit()

    # 2. Create Story
    story = Story(
        headline="Frontier Reasoning Model Breakthrough",
        summary="Novel reinforcement learning architecture demonstrated on complex math tasks.",
        key_takeaway="Compute scaling during inference delivers substantial accuracy gains.",
        why_it_matters="Redefines post-training paradigm for reasoning tasks.",
        importance_score=0.92,
        category="Model Release",
        is_curated=True,
        published_at=datetime.now(timezone.utc),
    )
    await story_repo.add(story)
    await db_session.commit()

    # 3. Create ContentItem linked to Source and Story
    now = datetime.now(timezone.utc)
    item = ContentItem(
        source_id=source.id,
        story_id=story.id,
        canonical_url=f"https://openai.com/index/test-{uuid.uuid4().hex[:6]}",
        title="Introducing Next-Gen Reasoning",
        raw_content="Today we announce our latest research breakthrough in reasoning.",
        author="OpenAI Research",
        published_at=now,
        fetched_at=now,
        content_hash=f"hash_{uuid.uuid4().hex}",
        processing_state="PROCESSED",
        metadata_json={"tags": ["reasoning", "rl"]},
    )
    await content_repo.add(item)
    await db_session.commit()

    # 4. Verify ContentItem lookups
    by_url = await content_repo.get_by_canonical_url(item.canonical_url)
    assert by_url is not None
    assert by_url.title == "Introducing Next-Gen Reasoning"
    assert by_url.source_id == source.id

    by_hash = await content_repo.get_by_content_hash(item.content_hash)
    assert by_hash is not None
    assert by_hash.id == item.id

    # 5. Verify Story eagerly loads articles with provenance
    loaded_story = await story_repo.get_with_articles(story.id)
    assert loaded_story is not None
    assert len(loaded_story.articles) == 1
    assert loaded_story.articles[0].id == item.id
