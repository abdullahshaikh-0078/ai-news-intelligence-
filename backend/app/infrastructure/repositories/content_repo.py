from datetime import datetime
from typing import List, Optional, Tuple
import uuid
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.repositories.base import BaseRepository


class ContentRepository(BaseRepository[ContentItem]):
    """Repository handling ContentItem persistence and lookups."""

    def __init__(self, session: AsyncSession):
        super().__init__(ContentItem, session)

    get = BaseRepository.get_by_id

    async def get_by_canonical_url(self, canonical_url: str) -> Optional[ContentItem]:
        """Lookup an item by its normalized canonical URL."""
        result = await self.session.execute(
            select(ContentItem).where(ContentItem.canonical_url == canonical_url)
        )
        return result.scalars().first()

    async def get_by_external_id(self, source_id: uuid.UUID, external_id: str) -> Optional[ContentItem]:
        """Lookup an item by its source-scoped external ID / GUID."""
        result = await self.session.execute(
            select(ContentItem).where(
                ContentItem.source_id == source_id,
                ContentItem.external_id == external_id,
            )
        )
        return result.scalars().first()

    async def get_by_content_hash(self, content_hash: str) -> Optional[ContentItem]:
        """Lookup an item by its exact content fingerprint hash."""
        result = await self.session.execute(
            select(ContentItem).where(ContentItem.content_hash == content_hash)
        )
        return result.scalars().first()

    async def list_by_source(self, source_id: uuid.UUID, limit: int = 50) -> List[ContentItem]:
        """List content items from a specific source."""
        result = await self.session.execute(
            select(ContentItem)
            .where(ContentItem.source_id == source_id)
            .order_by(ContentItem.published_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_by_source(self, source_id: uuid.UUID) -> int:
        """Count total ingested articles from a specific source."""
        result = await self.session.execute(
            select(func.count()).select_from(ContentItem).where(ContentItem.source_id == source_id)
        )
        return result.scalar_one()

    async def list_unclustered(self, limit: int = 100) -> List[ContentItem]:
        """List items not yet assigned to a parent story."""
        result = await self.session.execute(
            select(ContentItem)
            .where(ContentItem.story_id.is_(None))
            .order_by(ContentItem.published_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def update(self, item: ContentItem) -> ContentItem:
        """Persist modifications to a ContentItem."""
        self.session.add(item)
        await self.session.flush()
        return item

    async def list_by_content_type(self, content_type: str, limit: int = 50) -> List[ContentItem]:
        """List items matching a specific canonical content type."""
        result = await self.session.execute(
            select(ContentItem)
            .where(ContentItem.content_type == content_type)
            .order_by(ContentItem.published_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_pending_ai_processing(self, limit: int = 50) -> List[ContentItem]:
        """List content items awaiting AI processing."""
        result = await self.session.execute(
            select(ContentItem)
            .where(ContentItem.processing_state.in_(["PENDING", "RAW"]))
            .order_by(ContentItem.published_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_ai_processing_counts(self) -> dict:
        """Get aggregate counts of content items grouped by processing_state."""
        result = await self.session.execute(
            select(ContentItem.processing_state, func.count())
            .group_by(ContentItem.processing_state)
        )
        counts = {row[0].upper(): row[1] for row in result.all()}
        # Normalize RAW into PENDING if any
        if "RAW" in counts:
            counts["PENDING"] = counts.get("PENDING", 0) + counts.pop("RAW")
        total = sum(counts.values())
        return {
            "pending": counts.get("PENDING", 0),
            "processing": counts.get("PROCESSING", 0),
            "completed": counts.get("COMPLETED", 0),
            "failed": counts.get("FAILED", 0),
            "total": total,
        }

    async def get_content_with_source(self, content_id: uuid.UUID) -> Optional[ContentItem]:
        """Fetch a single ContentItem eagerly loading its parent Source."""
        stmt = (
            select(ContentItem)
            .options(selectinload(ContentItem.source))
            .where(ContentItem.id == content_id)
        )
        return (await self.session.execute(stmt)).scalars().first()

    async def list_content_paginated(
        self,
        content_type: Optional[str] = None,
        source_id: Optional[uuid.UUID] = None,
        published_after: Optional[datetime] = None,
        published_before: Optional[datetime] = None,
        order_by: str = "published_at_desc",
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[ContentItem], int]:
        """Fetch paginated, filtered ContentItems with eager loaded sources."""
        base_stmt = select(ContentItem).options(selectinload(ContentItem.source))
        count_stmt = select(func.count(ContentItem.id))

        filters = []
        if content_type:
            filters.append(ContentItem.content_type == content_type.upper())
        if source_id:
            filters.append(ContentItem.source_id == source_id)
        if published_after:
            filters.append(ContentItem.published_at >= published_after)
        if published_before:
            filters.append(ContentItem.published_at <= published_before)

        if filters:
            base_stmt = base_stmt.where(*filters)
            count_stmt = count_stmt.where(*filters)

        total = (await self.session.execute(count_stmt)).scalar_one()

        if order_by == "relevance_desc":
            base_stmt = base_stmt.order_by(
                ContentItem.ai_relevance_score.desc().nullslast(),
                ContentItem.published_at.desc(),
            )
        elif order_by == "published_at_asc":
            base_stmt = base_stmt.order_by(ContentItem.published_at.asc())
        else:
            base_stmt = base_stmt.order_by(ContentItem.published_at.desc())

        bounded_page = max(1, page)
        bounded_size = min(max(1, page_size), 100)
        offset = (bounded_page - 1) * bounded_size

        items = list(
            (await self.session.execute(base_stmt.limit(bounded_size).offset(offset))).scalars().all()
        )
        return items, total


