import pytest
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from app.infrastructure.models.source import Source
from app.infrastructure.repositories.source_repo import SourceRepository


@pytest.mark.asyncio
async def test_source_repository_crud(db_session: AsyncSession):
    """Test full CRUD operations on SourceRepository."""
    repo = SourceRepository(db_session)
    test_slug = f"repo-test-{uuid.uuid4().hex[:8]}"
    test_url = f"https://example.com/feed/{test_slug}"

    # 1. Create
    source = Source(
        name="Repo Test Source",
        slug=test_slug,
        type="RSS",
        url=test_url,
        enabled=True,
        language="en",
        reliability_score=0.85,
        fetch_interval_minutes=30,
        config={"key": "value"},
    )
    created = await repo.add(source)
    assert created.id is not None
    assert created.slug == test_slug

    # 2. Retrieve by ID
    fetched_by_id = await repo.get_by_id(created.id)
    assert fetched_by_id is not None
    assert fetched_by_id.name == "Repo Test Source"

    # 3. Retrieve by Slug
    fetched_by_slug = await repo.get_by_slug(test_slug)
    assert fetched_by_slug is not None
    assert fetched_by_slug.id == created.id

    # 4. Retrieve by URL
    fetched_by_url = await repo.get_by_url(test_url)
    assert fetched_by_url is not None
    assert fetched_by_url.id == created.id

    # 5. Update
    source.name = "Updated Repo Source"
    source.reliability_score = 0.95
    updated = await repo.update(source)
    assert updated.name == "Updated Repo Source"
    assert updated.reliability_score == 0.95

    # 6. Disable & Enable
    disabled = await repo.set_enabled(source, False)
    assert disabled.enabled is False
    enabled = await repo.set_enabled(source, True)
    assert enabled.enabled is True

    # 7. Delete
    await repo.delete(source)
    deleted = await repo.get_by_id(created.id)
    assert deleted is None


@pytest.mark.asyncio
async def test_source_repository_filtering_and_pagination(db_session: AsyncSession):
    """Test listing with filters and pagination."""
    repo = SourceRepository(db_session)
    prefix = uuid.uuid4().hex[:6]

    # Insert 3 sources: 2 RSS (1 enabled, 1 disabled), 1 ARXIV (enabled)
    s1 = Source(
        name=f"Source 1 {prefix}",
        slug=f"s1-{prefix}",
        type="RSS",
        url=f"https://example.com/s1-{prefix}",
        enabled=True,
    )
    s2 = Source(
        name=f"Source 2 {prefix}",
        slug=f"s2-{prefix}",
        type="RSS",
        url=f"https://example.com/s2-{prefix}",
        enabled=False,
    )
    s3 = Source(
        name=f"Source 3 {prefix}",
        slug=f"s3-{prefix}",
        type="ARXIV",
        url=f"https://example.com/s3-{prefix}",
        enabled=True,
    )
    await repo.add(s1)
    await repo.add(s2)
    await repo.add(s3)

    # Filter by type RSS
    rss_items, rss_total = await repo.list_sources(source_type="RSS", skip=0, limit=10)
    assert any(s.slug == f"s1-{prefix}" for s in rss_items)
    assert any(s.slug == f"s2-{prefix}" for s in rss_items)
    assert not any(s.slug == f"s3-{prefix}" for s in rss_items)

    # Filter by enabled=True
    enabled_items, enabled_total = await repo.list_sources(enabled=True, skip=0, limit=10)
    assert any(s.slug == f"s1-{prefix}" for s in enabled_items)
    assert any(s.slug == f"s3-{prefix}" for s in enabled_items)
    assert not any(s.slug == f"s2-{prefix}" for s in enabled_items)

    # Pagination test
    paged_items, total_count = await repo.list_sources(skip=0, limit=1)
    assert len(paged_items) == 1
    assert total_count >= 3
