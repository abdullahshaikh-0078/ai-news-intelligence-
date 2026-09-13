from collections import defaultdict
from datetime import datetime, timezone
import time
from typing import Any, Dict, List, Optional
from uuid import UUID
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.domain.models.curation import (
    CuratedStoryResponse,
    StoryCurationRunResponse,
)
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.story import Story
from app.infrastructure.models.story_content_item import StoryContentItem
from app.services.ranking_service import StoryRankingService


class StoryCurationService:
    """Service managing deterministic editorial curation and feed composition."""

    def __init__(
        self,
        session: AsyncSession,
        default_limit: Optional[int] = None,
        min_score: Optional[float] = None,
        max_per_topic: Optional[int] = None,
        max_per_source: Optional[int] = None,
        diversity_enabled: Optional[bool] = None,
    ):
        self.session = session
        self.default_limit = default_limit or settings.CURATION_DEFAULT_LIMIT
        self.min_score = min_score if min_score is not None else settings.CURATION_MIN_SCORE
        self.max_per_topic = max_per_topic or settings.CURATION_MAX_PER_TOPIC
        self.max_per_source = max_per_source or settings.CURATION_MAX_PER_SOURCE
        self.diversity_enabled = (
            diversity_enabled if diversity_enabled is not None else settings.CURATION_DIVERSITY_ENABLED
        )
        self.ranking_service = StoryRankingService(session=session)

    async def list_curated_stories(
        self,
        limit: int = 20,
        offset: int = 0,
        topic: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> List[CuratedStoryResponse]:
        """Retrieve currently curated stories for the news feed with optional topic and content type filtering."""
        stmt = (
            select(Story)
            .options(
                selectinload(Story.canonical_content_item).joinedload(ContentItem.source),
                selectinload(Story.story_items),
            )
            .where(
                Story.is_curated.is_(True),
                Story.status != "ARCHIVED",
            )
        )

        if topic:
            stmt = stmt.where(Story.category.ilike(f"%{topic}%"))
        if content_type:
            stmt = stmt.join(Story.canonical_content_item).where(
                ContentItem.content_type == content_type.upper()
            )

        stmt = stmt.order_by(Story.ranking_score.desc(), Story.curated_at.desc().nullslast()).limit(limit).offset(offset)
        stories = list((await self.session.execute(stmt)).scalars().all())

        results: List[CuratedStoryResponse] = []
        for s in stories:
            count = len(s.story_items) if s.story_items else 1
            src_name = None
            canon_url = None
            c_type = "ARTICLE"
            topics = []
            if s.canonical_content_item:
                canon_url = s.canonical_content_item.canonical_url
                c_type = s.canonical_content_item.content_type
                if s.canonical_content_item.ai_topics and isinstance(s.canonical_content_item.ai_topics, list):
                    topics = s.canonical_content_item.ai_topics
                if s.canonical_content_item.source:
                    src_name = s.canonical_content_item.source.name
            if not topics and s.category:
                topics = [s.category]

            results.append(
                CuratedStoryResponse(
                    story_id=s.id,
                    title=s.headline,
                    summary=s.summary,
                    key_takeaway=s.key_takeaway,
                    why_it_matters=s.why_it_matters,
                    ranking_score=s.ranking_score,
                    category=s.category,
                    topics=topics,
                    canonical_url=canon_url,
                    content_type=c_type,
                    primary_source_name=src_name,
                    article_count=count,
                    published_at=s.published_at,
                    curated_at=s.curated_at,
                )
            )

        return results

    async def curate_feed(
        self,
        limit: Optional[int] = None,
        min_score: Optional[float] = None,
        max_per_topic: Optional[int] = None,
        max_per_source: Optional[int] = None,
        diversity_enabled: Optional[bool] = None,
        reference_time: Optional[datetime] = None,
    ) -> StoryCurationRunResponse:
        """
        Execute deterministic editorial curation pass.
        Selects top-ranked eligible stories while enforcing topic and source diversity caps.
        Guarantees zero modifications to ContentItems or vector embeddings.
        """
        t0 = time.perf_counter()
        target_limit = limit or self.default_limit
        cutoff_score = min_score if min_score is not None else self.min_score
        topic_cap = max_per_topic or self.max_per_topic
        source_cap = max_per_source or self.max_per_source
        enforce_diversity = diversity_enabled if diversity_enabled is not None else self.diversity_enabled
        now = reference_time or datetime.now(timezone.utc)

        # 1. Fetch candidate stories ordered by ranking score descending
        stmt = (
            select(Story)
            .options(
                selectinload(Story.canonical_content_item).joinedload(ContentItem.source),
                selectinload(Story.story_items),
            )
            .where(Story.status != "ARCHIVED")
            .order_by(Story.ranking_score.desc(), Story.published_at.desc())
            .limit(200)
        )
        candidates = list((await self.session.execute(stmt)).scalars().all())

        total_scanned = len(candidates)
        eligible_count = 0
        rejected_reasons: Dict[str, int] = defaultdict(int)
        curated_stories: List[Story] = []

        topic_counts: Dict[str, int] = defaultdict(int)
        source_counts: Dict[str, int] = defaultdict(int)

        # 2. Greedy selection pass
        for story in candidates:
            # Check minimum score cutoff
            if story.ranking_score < cutoff_score:
                rejected_reasons["below_min_score"] += 1
                continue

            eligible_count += 1

            # Determine primary topic
            topic = (story.category or "General AI").strip().lower()

            # Determine primary source ID
            source_id = None
            if story.canonical_content_item and story.canonical_content_item.source_id:
                source_id = str(story.canonical_content_item.source_id)
            else:
                source_id = "unknown_source"

            # Apply diversity constraints
            if enforce_diversity:
                if topic_counts[topic] >= topic_cap:
                    rejected_reasons["topic_cap_reached"] += 1
                    continue
                if source_counts[source_id] >= source_cap:
                    rejected_reasons["source_cap_reached"] += 1
                    continue

            # Accept story into curation
            topic_counts[topic] += 1
            source_counts[source_id] += 1
            curated_stories.append(story)

            if len(curated_stories) >= target_limit:
                break

        # 3. Update database state: set is_curated for selected, clear previous curated stories not selected
        curated_ids = {s.id for s in curated_stories}

        # Clear old curation flags
        await self.session.execute(
            update(Story)
            .where(Story.is_curated.is_(True), Story.id.not_in(curated_ids))
            .values(is_curated=False)
        )

        # Apply new curation flags with metadata
        for rank_idx, story in enumerate(curated_stories, start=1):
            story.is_curated = True
            story.curated_at = now
            story.curation_metadata = {
                "curated_at": now.isoformat(),
                "curation_rank": rank_idx,
                "score_at_curation": story.ranking_score,
                "topic": story.category,
                "cutoff_used": cutoff_score,
            }
            self.session.add(story)

        await self.session.commit()
        duration_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        return StoryCurationRunResponse(
            stories_scanned=total_scanned,
            eligible_count=eligible_count,
            curated_count=len(curated_stories),
            rejected_reasons=dict(rejected_reasons),
            duration_ms=duration_ms,
        )
