from datetime import datetime, timezone
import math
import time
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.exceptions import AppException
from app.domain.models.ranking import (
    RankingSignalExplanation,
    StoryRankedResponse,
    StoryRankingExplanation,
    StoryRankingRunResponse,
)
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.source import Source
from app.infrastructure.models.story import Story
from app.infrastructure.models.story_content_item import StoryContentItem
from app.infrastructure.repositories.story_repo import StoryRepository


class StoryRankingService:
    """Service managing deterministic multi-signal story ranking and scoring."""

    def __init__(
        self,
        session: AsyncSession,
        w_relevance: Optional[float] = None,
        w_authority: Optional[float] = None,
        w_recency: Optional[float] = None,
        w_coverage: Optional[float] = None,
        w_diversity: Optional[float] = None,
        w_engagement: Optional[float] = None,
        recency_half_life_hours: Optional[float] = None,
    ):
        self.session = session
        self.story_repo = StoryRepository(session)
        self.w_relevance = w_relevance if w_relevance is not None else settings.STORY_RANK_RELEVANCE_WEIGHT
        self.w_authority = w_authority if w_authority is not None else settings.STORY_RANK_AUTHORITY_WEIGHT
        self.w_recency = w_recency if w_recency is not None else settings.STORY_RANK_RECENCY_WEIGHT
        self.w_coverage = w_coverage if w_coverage is not None else settings.STORY_RANK_COVERAGE_WEIGHT
        self.w_diversity = w_diversity if w_diversity is not None else settings.STORY_RANK_DIVERSITY_WEIGHT
        self.w_engagement = w_engagement if w_engagement is not None else settings.STORY_RANK_ENGAGEMENT_WEIGHT
        self.half_life_hours = (
            recency_half_life_hours
            if recency_half_life_hours is not None
            else settings.STORY_RANK_RECENCY_HALF_LIFE_HOURS
        )

    # -------------------------------------------------------------
    # Signal Calculators
    # -------------------------------------------------------------

    def calculate_relevance(
        self, story: Story, member_items: List[ContentItem]
    ) -> Tuple[float, float, Dict[str, Any]]:
        """
        AI Relevance Signal: Evaluates topic importance and model relevance.
        Range: [0.0, 1.0].
        """
        # 1. Prefer canonical item's ai_relevance_score
        if story.canonical_content_item and story.canonical_content_item.ai_relevance_score is not None:
            raw_val = float(story.canonical_content_item.ai_relevance_score)
            source_used = "canonical_ai_relevance_score"
        else:
            # 2. Check member items with valid ai_relevance_score
            scores = [
                float(it.ai_relevance_score)
                for it in member_items
                if it.ai_relevance_score is not None
            ]
            if scores:
                raw_val = sum(scores) / len(scores)
                source_used = "average_member_ai_relevance_score"
            elif story.importance_score > 0.0:
                raw_val = float(story.importance_score)
                source_used = "story_importance_score"
            else:
                raw_val = 0.5  # Neutral baseline
                source_used = "default_neutral"

        norm_val = max(0.0, min(1.0, raw_val))
        return raw_val, norm_val, {"source_used": source_used}

    def calculate_authority(
        self, story: Story, member_items: List[ContentItem]
    ) -> Tuple[float, float, Dict[str, Any]]:
        """
        Source Authority Signal: Mean reliability score of distinct upstream sources.
        Range: [0.0, 1.0].
        """
        # Deduplicate sources by source_id
        unique_sources: Dict[UUID, Source] = {}
        for it in member_items:
            if it.source and it.source.id not in unique_sources:
                unique_sources[it.source.id] = it.source

        if not unique_sources:
            return 1.0, 1.0, {"sources_count": 0, "note": "default_no_sources"}

        reliabilities = [
            float(src.reliability_score)
            for src in unique_sources.values()
            if src.reliability_score is not None
        ]
        if not reliabilities:
            return 1.0, 1.0, {"sources_count": len(unique_sources), "note": "default_null_reliability"}

        raw_val = sum(reliabilities) / len(reliabilities)
        norm_val = max(0.0, min(1.0, raw_val))
        return raw_val, norm_val, {
            "distinct_sources_count": len(unique_sources),
            "sources": [src.name for src in unique_sources.values()],
        }

    def calculate_recency(
        self, story: Story, reference_time: Optional[datetime] = None
    ) -> Tuple[float, float, Dict[str, Any]]:
        """
        Recency Signal: Continuous exponential decay based on half-life.
        decay = exp(-ln(2) * dt / half_life). Range: [0.0, 1.0].
        """
        now = reference_time or datetime.now(timezone.utc)
        pub_time = story.published_at
        if not pub_time and story.canonical_content_item:
            pub_time = story.canonical_content_item.published_at
        if not pub_time:
            pub_time = story.created_at or now

        # Ensure tz-aware
        if pub_time.tzinfo is None:
            pub_time = pub_time.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        delta_hours = max(0.0, (now - pub_time).total_seconds() / 3600.0)

        # Exponential decay
        decay_factor = math.exp(-math.log(2) * delta_hours / self.half_life_hours)
        norm_val = max(0.0, min(1.0, decay_factor))

        return delta_hours, norm_val, {
            "delta_hours": round(delta_hours, 2),
            "half_life_hours": self.half_life_hours,
        }

    def calculate_coverage(
        self, member_items: List[ContentItem]
    ) -> Tuple[float, float, Dict[str, Any]]:
        """
        Coverage Signal: Breadth of independent reporting across distinct sources.
        Uses diminishing returns logarithmic scaling. Range: [0.0, 1.0].
        """
        distinct_source_ids = {it.source_id for it in member_items if it.source_id is not None}
        count = max(1, len(distinct_source_ids))

        # Target saturation is 4+ independent sources
        saturation = 4.0
        norm_val = min(1.0, math.log(1.0 + count) / math.log(1.0 + saturation))

        return float(count), norm_val, {
            "independent_sources_count": count,
            "saturation_target": saturation,
        }

    def calculate_diversity(
        self, member_items: List[ContentItem]
    ) -> Tuple[float, float, Dict[str, Any]]:
        """
        Content-Type Diversity Signal: Modest bonus for multi-modal coverage.
        Range: [0.0, 1.0].
        """
        content_types = {it.content_type for it in member_items if it.content_type}
        count = len(content_types)

        # Diversity is (count - 1) / 3 for up to 4 possible types
        norm_val = max(0.0, min(1.0, (count - 1) / 3.0)) if count > 0 else 0.0

        return float(count), norm_val, {
            "distinct_content_types": list(content_types),
            "distinct_types_count": count,
        }

    def calculate_engagement(
        self, member_items: List[ContentItem]
    ) -> Tuple[float, float, Dict[str, Any]]:
        """
        Community Engagement Signal: Bounded log-scaled community interest.
        Aggregates points, comments, likes, and views across member items.
        Range: [0.0, 1.0].
        """
        raw_engagement = 0.0
        for it in member_items:
            meta = it.metadata_json or {}
            if isinstance(meta, dict):
                score = float(meta.get("score") or meta.get("points") or 0.0)
                comments = float(meta.get("comments") or meta.get("comment_count") or 0.0)
                likes = float(meta.get("like_count") or 0.0)
                views = float(meta.get("view_count") or 0.0)
                raw_engagement += score + (2.0 * comments) + likes + (0.01 * views)

        if raw_engagement <= 0.0:
            return 0.0, 0.0, {"raw_score": 0.0, "note": "no_engagement_data"}

        # Soft log saturation at 500
        target = 500.0
        norm_val = min(1.0, math.log(1.0 + raw_engagement) / math.log(1.0 + target))

        return raw_engagement, norm_val, {
            "raw_score": round(raw_engagement, 2),
            "target_saturation": target,
        }

    # -------------------------------------------------------------
    # Overall Scoring & Persistence
    # -------------------------------------------------------------

    def evaluate_story_signals(
        self,
        story: Story,
        member_items: List[ContentItem],
        reference_time: Optional[datetime] = None,
    ) -> StoryRankingExplanation:
        """Compute all 6 transparent signals and the weighted total score for a story."""
        now = reference_time or datetime.now(timezone.utc)

        # 1. Compute individual signals
        raw_rel, norm_rel, det_rel = self.calculate_relevance(story, member_items)
        raw_auth, norm_auth, det_auth = self.calculate_authority(story, member_items)
        raw_rec, norm_rec, det_rec = self.calculate_recency(story, reference_time=now)
        raw_cov, norm_cov, det_cov = self.calculate_coverage(member_items)
        raw_div, norm_div, det_div = self.calculate_diversity(member_items)
        raw_eng, norm_eng, det_eng = self.calculate_engagement(member_items)

        # 2. Structure signal explanations
        signals: Dict[str, RankingSignalExplanation] = {
            "relevance": RankingSignalExplanation(
                name="relevance",
                raw_value=round(raw_rel, 4),
                normalized_value=round(norm_rel, 4),
                weight=self.w_relevance,
                weighted_score=round(norm_rel * self.w_relevance, 4),
                details=det_rel,
            ),
            "authority": RankingSignalExplanation(
                name="authority",
                raw_value=round(raw_auth, 4),
                normalized_value=round(norm_auth, 4),
                weight=self.w_authority,
                weighted_score=round(norm_auth * self.w_authority, 4),
                details=det_auth,
            ),
            "recency": RankingSignalExplanation(
                name="recency",
                raw_value=round(raw_rec, 4),
                normalized_value=round(norm_rec, 4),
                weight=self.w_recency,
                weighted_score=round(norm_rec * self.w_recency, 4),
                details=det_rec,
            ),
            "coverage": RankingSignalExplanation(
                name="coverage",
                raw_value=round(raw_cov, 4),
                normalized_value=round(norm_cov, 4),
                weight=self.w_coverage,
                weighted_score=round(norm_cov * self.w_coverage, 4),
                details=det_cov,
            ),
            "diversity": RankingSignalExplanation(
                name="diversity",
                raw_value=round(raw_div, 4),
                normalized_value=round(norm_div, 4),
                weight=self.w_diversity,
                weighted_score=round(norm_div * self.w_diversity, 4),
                details=det_div,
            ),
            "engagement": RankingSignalExplanation(
                name="engagement",
                raw_value=round(raw_eng, 4),
                normalized_value=round(norm_eng, 4),
                weight=self.w_engagement,
                weighted_score=round(norm_eng * self.w_engagement, 4),
                details=det_eng,
            ),
        }

        # 3. Weighted total score
        total_weight = sum(s.weight for s in signals.values())
        if total_weight <= 0.0:
            total_weight = 1.0

        weighted_sum = sum(s.weighted_score for s in signals.values())
        final_score = max(0.0, min(1.0, weighted_sum / total_weight))

        return StoryRankingExplanation(
            story_id=story.id,
            headline=story.headline,
            total_score=round(final_score, 4),
            signals=signals,
            evaluated_at=now,
            weights_sum=round(total_weight, 4),
        )

    async def get_story_with_members(self, story_id: UUID) -> Tuple[Optional[Story], List[ContentItem]]:
        """Fetch story with member content items and their sources."""
        stmt = (
            select(Story)
            .options(
                selectinload(Story.story_items)
                .joinedload(StoryContentItem.content_item)
                .joinedload(ContentItem.source),
                selectinload(Story.canonical_content_item).joinedload(ContentItem.source),
            )
            .where(Story.id == story_id)
        )
        story = (await self.session.execute(stmt)).scalars().first()
        if not story:
            return None, []

        member_items = [assoc.content_item for assoc in story.story_items if assoc.content_item]
        # Ensure canonical item is included if not in story_items
        if story.canonical_content_item and story.canonical_content_item not in member_items:
            member_items.append(story.canonical_content_item)

        return story, member_items

    async def rank_story(
        self, story_id: UUID, reference_time: Optional[datetime] = None
    ) -> StoryRankingExplanation:
        """Evaluate, persist, and return ranking explanation for a specific story."""
        story, member_items = await self.get_story_with_members(story_id)
        if not story:
            raise AppException("NOT_FOUND", f"Story with ID {story_id} not found", status_code=404)

        explanation = self.evaluate_story_signals(story, member_items, reference_time=reference_time)

        # Persist ranking score and explanation
        story.ranking_score = explanation.total_score
        story.ranking_updated_at = explanation.evaluated_at
        story.ranking_metadata = explanation.model_dump(mode="json")
        self.session.add(story)
        await self.session.flush()

        return explanation

    async def rank_batch(
        self, limit: int = 50, force_recalculate: bool = False
    ) -> StoryRankingRunResponse:
        """
        Execute bounded batch ranking across stories.
        Guarantees zero modifications to ContentItems or vector embeddings.
        """
        t0 = time.perf_counter()
        bounded_limit = min(max(1, limit), 200)

        stmt = select(Story.id).where(Story.status != "ARCHIVED")
        if not force_recalculate:
            stmt = stmt.order_by(Story.ranking_updated_at.is_(None).desc(), Story.ranking_updated_at.asc())
        else:
            stmt = stmt.order_by(Story.published_at.desc())

        stmt = stmt.limit(bounded_limit)
        story_ids = list((await self.session.execute(stmt)).scalars().all())

        ranked_count = 0
        now = datetime.now(timezone.utc)
        for s_id in story_ids:
            await self.rank_story(s_id, reference_time=now)
            ranked_count += 1

        await self.session.commit()
        duration_ms = round((time.perf_counter() - t0) * 1000.0, 2)

        return StoryRankingRunResponse(
            stories_scanned=len(story_ids),
            stories_ranked=ranked_count,
            duration_ms=duration_ms,
        )

    async def list_ranked_stories(
        self, limit: int = 20, offset: int = 0, min_score: Optional[float] = None
    ) -> List[StoryRankedResponse]:
        """List stories ordered descending by ranking score."""
        stmt = (
            select(Story)
            .options(
                selectinload(Story.canonical_content_item),
                selectinload(Story.story_items),
            )
            .where(Story.status != "ARCHIVED")
        )
        if min_score is not None:
            stmt = stmt.where(Story.ranking_score >= min_score)

        stmt = stmt.order_by(Story.ranking_score.desc(), Story.published_at.desc())
        stmt = stmt.limit(limit).offset(offset)

        stories = list((await self.session.execute(stmt)).scalars().all())
        results: List[StoryRankedResponse] = []
        for s in stories:
            count = len(s.story_items) if s.story_items else 1
            expl = None
            if s.ranking_metadata and "signals" in s.ranking_metadata:
                try:
                    expl = StoryRankingExplanation(**s.ranking_metadata)
                except Exception:
                    expl = None

            results.append(
                StoryRankedResponse(
                    story_id=s.id,
                    title=s.headline,
                    ranking_score=s.ranking_score,
                    category=s.category,
                    article_count=count,
                    published_at=s.published_at,
                    ranking_updated_at=s.ranking_updated_at,
                    explanation=expl,
                )
            )

        return results
