from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
import uuid
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.story import Story
from app.infrastructure.models.story_content_item import StoryContentItem
from app.infrastructure.repositories.base import BaseRepository


class StoryRepository(BaseRepository[Story]):
    """Repository managing Story entity persistence, relationships, and clustering lookups."""

    def __init__(self, session: AsyncSession):
        super().__init__(Story, session)

    get = BaseRepository.get_by_id

    async def get_with_items(self, story_id: uuid.UUID) -> Optional[Story]:
        """Fetch a story eagerly loading all member items and their upstream sources."""
        result = await self.session.execute(
            select(Story)
            .options(
                selectinload(Story.story_items).joinedload(StoryContentItem.content_item).joinedload(ContentItem.source),
                selectinload(Story.canonical_content_item),
            )
            .where(Story.id == story_id)
        )
        return result.scalars().first()

    async def get_with_articles(self, story_id: uuid.UUID) -> Optional[Story]:
        """Fetch a story eagerly loading its legacy articles relationship and upstream sources."""
        result = await self.session.execute(
            select(Story)
            .options(
                selectinload(Story.articles).joinedload(ContentItem.source),
                selectinload(Story.canonical_content_item),
            )
            .where(Story.id == story_id)
        )
        return result.scalars().first()

    async def create_story(
        self,
        title: str,
        summary: Optional[str] = None,
        key_takeaway: Optional[str] = None,
        why_it_matters: Optional[str] = None,
        canonical_item_id: Optional[uuid.UUID] = None,
        status: str = "ACTIVE",
        category: Optional[str] = None,
        importance_score: float = 0.0,
        published_at: Optional[datetime] = None,
        metadata_json: Optional[dict] = None,
    ) -> Story:
        """Create and persist a new Story entity."""
        story = Story(
            headline=title,
            summary=summary,
            key_takeaway=key_takeaway,
            why_it_matters=why_it_matters,
            canonical_content_item_id=canonical_item_id,
            status=status,
            category=category,
            importance_score=importance_score,
            published_at=published_at or datetime.now(timezone.utc),
            metadata_json=metadata_json or {},
        )
        self.session.add(story)
        await self.session.flush()
        return story

    async def add_item_to_story(
        self,
        story_id: uuid.UUID,
        content_item_id: uuid.UUID,
        is_canonical: bool = False,
        confidence_score: float = 1.0,
        metadata_json: Optional[dict] = None,
    ) -> StoryContentItem:
        """Link a ContentItem to a Story and update legacy story_id foreign key for compatibility."""
        stmt = select(StoryContentItem).where(
            StoryContentItem.story_id == story_id,
            StoryContentItem.content_item_id == content_item_id,
        )
        existing = (await self.session.execute(stmt)).scalars().first()

        if existing:
            if is_canonical:
                existing.is_canonical = True
            existing.confidence_score = confidence_score
            self.session.add(existing)
            await self.session.flush()
            association = existing
        else:
            association = StoryContentItem(
                story_id=story_id,
                content_item_id=content_item_id,
                is_canonical=is_canonical,
                confidence_score=confidence_score,
                metadata_json=metadata_json or {},
            )
            self.session.add(association)
            await self.session.flush()

        # Update ContentItem.story_id pointer for backward compatibility
        item_stmt = select(ContentItem).where(ContentItem.id == content_item_id)
        item = (await self.session.execute(item_stmt)).scalars().first()
        if item:
            item.story_id = story_id
            self.session.add(item)
            await self.session.flush()

        return association

    async def find_active_candidate_stories(
        self,
        reference_time: datetime,
        window_hours: int = 72,
    ) -> List[Story]:
        """
        Find active stories within the temporal clustering window that have a canonical item with embeddings.
        """
        min_time = reference_time - timedelta(hours=window_hours)
        max_time = reference_time + timedelta(hours=window_hours)

        stmt = (
            select(Story)
            .options(
                selectinload(Story.canonical_content_item),
                selectinload(Story.story_items),
            )
            .where(
                Story.status.in_(["ACTIVE", "UPDATED"]),
                Story.published_at >= min_time,
                Story.published_at <= max_time,
                Story.canonical_content_item_id.is_not(None),
            )
            .order_by(Story.published_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_stories(
        self,
        status: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> List[Story]:
        """List stories with pagination and optional status filtering."""
        stmt = (
            select(Story)
            .options(selectinload(Story.story_items))
        )
        if status:
            stmt = stmt.where(Story.status == status)

        stmt = stmt.order_by(Story.published_at.desc().nullslast()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_stories_paginated(
        self,
        status: Optional[str] = None,
        category: Optional[str] = None,
        min_ranking_score: Optional[float] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Story], int]:
        """List stories with standardized page/page_size pagination and filtering."""
        base_stmt = select(Story).options(
            selectinload(Story.story_items),
            selectinload(Story.canonical_content_item),
        )
        count_stmt = select(func.count(Story.id))

        filters = []
        if status:
            filters.append(Story.status == status.upper())
        if category:
            filters.append(Story.category.ilike(f"%{category}%"))
        if min_ranking_score is not None:
            filters.append(Story.ranking_score >= min_ranking_score)

        if filters:
            base_stmt = base_stmt.where(*filters)
            count_stmt = count_stmt.where(*filters)

        total = (await self.session.execute(count_stmt)).scalar_one()

        bounded_page = max(1, page)
        bounded_size = min(max(1, page_size), 100)
        offset = (bounded_page - 1) * bounded_size

        stmt = (
            base_stmt
            .order_by(Story.ranking_score.desc(), Story.published_at.desc().nullslast())
            .limit(bounded_size)
            .offset(offset)
        )
        items = list((await self.session.execute(stmt)).scalars().all())
        return items, total

    async def count_stories(self, status: Optional[str] = None) -> int:
        """Count total stories matching optional status filter."""
        stmt = select(func.count()).select_from(Story)
        if status:
            stmt = stmt.where(Story.status == status)
        return (await self.session.execute(stmt)).scalar_one()

    async def get_story_members(self, story_id: uuid.UUID) -> List[Tuple[StoryContentItem, ContentItem]]:
        """Retrieve member items with source evidence."""
        stmt = (
            select(StoryContentItem, ContentItem)
            .join(ContentItem, StoryContentItem.content_item_id == ContentItem.id)
            .options(selectinload(ContentItem.source))
            .where(StoryContentItem.story_id == story_id)
            .order_by(StoryContentItem.is_canonical.desc(), ContentItem.published_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.all())
