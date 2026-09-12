from typing import List, Optional, Tuple
import uuid
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.infrastructure.models.source import Source
from app.infrastructure.repositories.base import BaseRepository


class SourceRepository(BaseRepository[Source]):
    """Repository handling Source entity persistence and queries."""

    def __init__(self, session: AsyncSession):
        super().__init__(Source, session)

    async def get_by_slug(self, slug: str) -> Optional[Source]:
        """Lookup a source by its unique human-readable slug."""
        result = await self.session.execute(
            select(Source).where(Source.slug == slug)
        )
        return result.scalars().first()

    async def get_by_url(self, url: str) -> Optional[Source]:
        """Lookup a source by its exact URL."""
        result = await self.session.execute(
            select(Source).where(Source.url == url)
        )
        return result.scalars().first()

    async def list_sources(
        self,
        source_type: Optional[str] = None,
        enabled: Optional[bool] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Tuple[List[Source], int]:
        """Fetch paginated sources with optional type and enabled filtering, returning items and total count."""
        base_query = select(Source)
        count_query = select(func.count()).select_from(Source)

        if source_type:
            base_query = base_query.where(Source.type == source_type)
            count_query = count_query.where(Source.type == source_type)

        if enabled is not None:
            base_query = base_query.where(Source.enabled == enabled)
            count_query = count_query.where(Source.enabled == enabled)

        total_result = await self.session.execute(count_query)
        total = total_result.scalar_one()

        items_result = await self.session.execute(
            base_query.order_by(Source.created_at.desc()).offset(skip).limit(limit)
        )
        items = list(items_result.scalars().all())

        return items, total

    async def list_enabled(self) -> List[Source]:
        """Fetch all currently enabled sources."""
        result = await self.session.execute(
            select(Source).where(Source.enabled == True).order_by(Source.name.asc())
        )
        return list(result.scalars().all())

    async def set_enabled(self, source: Source, enabled: bool) -> Source:
        """Toggle enabled flag for a source."""
        source.enabled = enabled
        self.session.add(source)
        await self.session.flush()
        return source

    async def update(self, source: Source) -> Source:
        """Persist modifications to a source."""
        self.session.add(source)
        await self.session.flush()
        return source

    async def list_by_type(self, source_type: str) -> List[Source]:
        """Fetch all sources matching a given type."""
        result = await self.session.execute(
            select(Source).where(Source.type == source_type).order_by(Source.name.asc())
        )
        return list(result.scalars().all())

