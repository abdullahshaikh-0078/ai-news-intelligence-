import pytest
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.exceptions import EntityConflictError, EntityNotFoundError
from app.domain.models.source import SourceType
from app.domain.schemas.source import SourceCreate, SourceUpdate
from app.infrastructure.repositories.source_repo import SourceRepository
from app.services.source_service import SourceService


@pytest.mark.asyncio
async def test_duplicate_url_prevention(db_session: AsyncSession):
    """Test that creating a source with an existing URL raises EntityConflictError."""
    repo = SourceRepository(db_session)
    service = SourceService(repo)
    shared_url = f"https://example.com/unique-feed-{uuid.uuid4().hex[:8]}"

    payload1 = SourceCreate(
        name="Source Alpha",
        type=SourceType.RSS,
        url=shared_url,
    )
    await service.create_source(payload1)

    # Attempt to create duplicate URL
    payload2 = SourceCreate(
        name="Source Beta",
        type=SourceType.RSS,
        url=shared_url,
    )
    with pytest.raises(EntityConflictError) as exc_info:
        await service.create_source(payload2)
    assert "already exists" in str(exc_info.value)


@pytest.mark.asyncio
async def test_explicit_duplicate_slug_prevention(db_session: AsyncSession):
    """Test that specifying an already existing custom slug raises EntityConflictError."""
    repo = SourceRepository(db_session)
    service = SourceService(repo)
    unique_slug = f"custom-slug-{uuid.uuid4().hex[:6]}"

    payload1 = SourceCreate(
        name="Custom Source 1",
        slug=unique_slug,
        type=SourceType.WEB,
        url="https://example.com/page1",
    )
    await service.create_source(payload1)

    payload2 = SourceCreate(
        name="Custom Source 2",
        slug=unique_slug,
        type=SourceType.WEB,
        url="https://example.com/page2",
    )
    with pytest.raises(EntityConflictError) as exc_info:
        await service.create_source(payload2)
    assert "already exists" in str(exc_info.value)


@pytest.mark.asyncio
async def test_auto_disambiguate_slug(db_session: AsyncSession):
    """Test that auto-generated slugs with matching names are safely disambiguated."""
    repo = SourceRepository(db_session)
    service = SourceService(repo)

    payload1 = SourceCreate(
        name="Shared Name",
        type=SourceType.ARXIV,
        url="https://example.com/arxiv1",
    )
    s1 = await service.create_source(payload1)

    payload2 = SourceCreate(
        name="Shared Name",
        type=SourceType.ARXIV,
        url="https://example.com/arxiv2",
    )
    s2 = await service.create_source(payload2)

    assert s1.slug.startswith("shared-name")
    assert s2.slug.startswith("shared-name")
    assert s1.slug != s2.slug


@pytest.mark.asyncio
async def test_service_enable_disable(db_session: AsyncSession):
    """Test service enable and disable transitions."""
    repo = SourceRepository(db_session)
    service = SourceService(repo)

    source = await service.create_source(
        SourceCreate(
            name="Toggle Source",
            type=SourceType.YOUTUBE,
            url=f"https://example.com/yt-{uuid.uuid4().hex[:6]}",
            enabled=True,
        )
    )
    assert source.enabled is True

    disabled = await service.disable_source(source.id)
    assert disabled.enabled is False

    enabled = await service.enable_source(source.id)
    assert enabled.enabled is True


@pytest.mark.asyncio
async def test_service_not_found(db_session: AsyncSession):
    """Test service raises EntityNotFoundError for non-existent UUID."""
    repo = SourceRepository(db_session)
    service = SourceService(repo)
    random_id = uuid.uuid4()

    with pytest.raises(EntityNotFoundError):
        await service.get_source(random_id)


@pytest.mark.asyncio
async def test_seed_default_sources_idempotency(db_session: AsyncSession):
    """Test that baseline sources can be seeded repeatedly without errors or duplicates."""
    repo = SourceRepository(db_session)
    service = SourceService(repo)

    # First seeding run
    seeded_first = await service.seed_default_sources()
    assert len(seeded_first) == 10

    # Second seeding run must be idempotent
    seeded_second = await service.seed_default_sources()
    assert len(seeded_second) == 10

    # IDs should match
    first_ids = {s.id for s in seeded_first}
    second_ids = {s.id for s in seeded_second}
    assert first_ids == second_ids
