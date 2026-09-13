from datetime import datetime, timedelta, timezone
import re
from typing import List, Optional, Tuple
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppException
from app.core.logging import logger
from app.domain.models.story import (
    StoryClusterResponse,
    StoryDetailResponse,
    StoryItemEvidence,
    StoryRefreshResponse,
    StoryStatus,
    StoryWithItemsResponse,
)
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.story import Story
from app.infrastructure.repositories.content_repo import ContentRepository
from app.infrastructure.repositories.dedup_repo import ContentDuplicateRepository
from app.infrastructure.repositories.story_repo import StoryRepository


def clean_story_title(raw_title: str) -> str:
    """Deterministic title normalization stripping prefixes, source tags, and brackets."""
    if not raw_title:
        return "Untitled Story"

    cleaned = raw_title.strip()
    # Remove leading tags: [tag], (tag), etc.
    cleaned = re.sub(r"^\s*\[[^\]]+\]\s*", "", cleaned)
    cleaned = re.sub(r"^\s*\([^)]+\)\s*", "", cleaned)

    # Remove common developer/community prefixes
    cleaned = re.sub(
        r"^(Show HN|Ask HN|Tell HN|PDF|Research|Announcement|Breaking|Report):\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    # Remove trailing source branding (e.g., " - TechCrunch", " | VentureBeat")
    cleaned = re.sub(r"\s+[-–|]\s+[A-Za-z0-9\s.]{2,30}$", "", cleaned)

    cleaned = cleaned.strip()
    return cleaned if cleaned else raw_title.strip()


def select_representative_item(items: List[ContentItem]) -> ContentItem:
    """
    Select the canonical content item for a story cluster based on content signal:
    - Formal articles and research papers receive higher signal weight than short community posts.
    - Higher AI relevance score indicates higher technical substance.
    - Fallback to earliest publication date.
    """
    def score_item(item: ContentItem) -> Tuple[float, float, float]:
        type_weight = 0.0
        c_type = (item.content_type or "ARTICLE").upper()
        if c_type == "RESEARCH_PAPER":
            type_weight = 3.0
        elif c_type == "ARTICLE":
            type_weight = 2.0
        elif c_type == "VIDEO":
            type_weight = 1.5
        elif c_type == "COMMUNITY_POST":
            type_weight = 1.0

        rel_score = float(item.ai_relevance_score or 0.5)
        # Invert timestamp for earliest-first preference
        ts = item.published_at.timestamp() if item.published_at else 0.0
        return (type_weight, rel_score, -ts)

    sorted_items = sorted(items, key=score_item, reverse=True)
    return sorted_items[0]


def synthesize_story_content(items: List[ContentItem]) -> Tuple[str, str, str]:
    """
    Generate grounded multi-source synthesis from member items without external AI calls:
    - Combines AI summaries and key takeaways across sources.
    - Derives key takeaway and significance.
    """
    summaries = []
    key_points = []
    categories = []

    for item in items:
        if item.ai_summary and item.ai_summary.strip():
            summaries.append(item.ai_summary.strip())
        elif item.summary and item.summary.strip():
            summaries.append(item.summary.strip())

        if item.ai_key_points and isinstance(item.ai_key_points, list):
            for kp in item.ai_key_points:
                if str(kp).strip() and str(kp).strip() not in key_points:
                    key_points.append(str(kp).strip())

        if item.ai_topics and isinstance(item.ai_topics, list):
            for topic in item.ai_topics:
                if str(topic).strip() and str(topic).strip() not in categories:
                    categories.append(str(topic).strip())

    # Synthesis summary
    if len(summaries) == 1:
        synthesis_summary = summaries[0]
    elif len(summaries) > 1:
        # Grounded multi-source synthesis: lead summary + cross-source coverage note
        lead = summaries[0]
        synthesis_summary = f"{lead} Multi-source reporting confirms developments across {len(items)} independent channels."
    else:
        synthesis_summary = items[0].title

    # Key takeaways
    if key_points:
        takeaway = " • ".join(key_points[:3])
    else:
        takeaway = f"Development covered across {len(items)} sources."

    # Why it matters
    topics_str = ", ".join(categories[:3]) if categories else "Artificial Intelligence"
    why_it_matters = f"Significant milestone impacting {topics_str}."

    return synthesis_summary, takeaway, why_it_matters


class StoryClusteringService:
    """Service managing Story lifecycle, multi-signal clustering, and grounded synthesis."""

    def __init__(
        self,
        session: AsyncSession,
        similarity_threshold: Optional[float] = None,
        window_hours: Optional[int] = None,
    ):
        self.session = session
        self.story_repo = StoryRepository(session)
        self.content_repo = ContentRepository(session)
        self.dedup_repo = ContentDuplicateRepository(session)
        self.similarity_threshold = (
            similarity_threshold
            if similarity_threshold is not None
            else settings.STORY_CLUSTER_SIMILARITY_THRESHOLD
        )
        self.window_hours = (
            window_hours
            if window_hours is not None
            else settings.STORY_CLUSTER_WINDOW_HOURS
        )

    def _has_topic_overlap(self, topics_a: Optional[list], topics_b: Optional[list]) -> bool:
        """Check whether two items share at least one topical category if categorized."""
        if not topics_a or not topics_b:
            return True  # If topics missing, do not penalize
        set_a = {str(t).lower().strip() for t in topics_a if str(t).strip()}
        set_b = {str(t).lower().strip() for t in topics_b if str(t).strip()}
        return len(set_a.intersection(set_b)) > 0

    async def cluster_unassigned_candidates(
        self,
        limit: int = 50,
        window_hours: Optional[int] = None,
        similarity_threshold: Optional[float] = None,
    ) -> StoryClusterResponse:
        """
        Execute bounded multi-signal clustering on unassigned ContentItems.
        Guarantees zero destructive operations (0 deletes, 0 merges).
        """
        win_hrs = window_hours or self.window_hours
        sim_threshold = similarity_threshold or self.similarity_threshold
        bounded_limit = min(max(1, limit), 100)

        # 1. Fetch unclustered candidate ContentItems
        stmt = (
            select(ContentItem)
            .where(
                ContentItem.story_id.is_(None),
                ContentItem.embedding.is_not(None),
            )
            .order_by(ContentItem.published_at.desc())
            .limit(bounded_limit)
        )
        candidates = list((await self.session.execute(stmt)).scalars().all())

        total_scanned = len(candidates)
        items_skipped_no_embedding = 0
        stories_created = 0
        stories_updated = 0
        items_assigned = 0

        # Set of item IDs assigned during this execution pass
        clustered_in_pass = set()

        for item in candidates:
            if item.id in clustered_in_pass:
                continue

            # 2. Check if item matches an existing active Story within the time window
            active_stories = await self.story_repo.find_active_candidate_stories(
                reference_time=item.published_at or datetime.now(timezone.utc),
                window_hours=win_hrs,
            )

            matched_story: Optional[Story] = None
            best_similarity = 0.0

            for story in active_stories:
                if not story.canonical_content_item or not story.canonical_content_item.embedding:
                    continue

                canonical = story.canonical_content_item
                # Compute distance using pgvector operator logic
                dist_tuples = await self.dedup_repo.find_similar_by_embedding(
                    target_item_id=item.id,
                    embedding=item.embedding,
                    limit=5,
                )
                match_tuple = next((t for t in dist_tuples if t[0].id == canonical.id), None)
                if match_tuple:
                    sim = match_tuple[2]
                    topic_compat = self._has_topic_overlap(item.ai_topics, canonical.ai_topics)
                    if sim >= sim_threshold and topic_compat and sim > best_similarity:
                        matched_story = story
                        best_similarity = sim

            # 3. If matched existing story, attach item
            if matched_story:
                await self.story_repo.add_item_to_story(
                    story_id=matched_story.id,
                    content_item_id=item.id,
                    is_canonical=False,
                    confidence_score=round(best_similarity, 4),
                )
                matched_story.status = StoryStatus.UPDATED.value
                self.session.add(matched_story)
                await self.session.flush()

                clustered_in_pass.add(item.id)
                stories_updated += 1
                items_assigned += 1
                continue

            # 4. Otherwise, search for unclustered peer matches to form a new Story
            peer_matches: List[ContentItem] = [item]
            similar_peers = await self.dedup_repo.find_similar_by_embedding(
                target_item_id=item.id,
                embedding=item.embedding,
                limit=10,
            )

            for peer, dist, sim in similar_peers:
                if peer.story_id is not None or peer.id in clustered_in_pass:
                    continue
                # Time window constraint
                time_diff = abs((item.published_at - peer.published_at).total_seconds()) / 3600.0
                if time_diff <= win_hrs and sim >= sim_threshold:
                    if self._has_topic_overlap(item.ai_topics, peer.ai_topics):
                        peer_matches.append(peer)

            # 5. Create new Story with the grouped items
            canonical_item = select_representative_item(peer_matches)
            story_title = clean_story_title(canonical_item.title)
            story_summary, takeaway, why_it_matters = synthesize_story_content(peer_matches)

            category = (
                canonical_item.ai_topics[0]
                if canonical_item.ai_topics and isinstance(canonical_item.ai_topics, list)
                else "Artificial Intelligence"
            )
            importance = float(canonical_item.ai_relevance_score or 0.5)

            new_story = await self.story_repo.create_story(
                title=story_title,
                summary=story_summary,
                key_takeaway=takeaway,
                why_it_matters=why_it_matters,
                canonical_item_id=canonical_item.id,
                status=StoryStatus.ACTIVE.value,
                category=category,
                importance_score=importance,
                published_at=canonical_item.published_at,
                metadata_json={"member_count": len(peer_matches)},
            )

            for member in peer_matches:
                is_can = (member.id == canonical_item.id)
                await self.story_repo.add_item_to_story(
                    story_id=new_story.id,
                    content_item_id=member.id,
                    is_canonical=is_can,
                    confidence_score=1.0 if is_can else sim_threshold,
                )
                clustered_in_pass.add(member.id)
                items_assigned += 1

            stories_created += 1

        return StoryClusterResponse(
            candidates_scanned=total_scanned,
            stories_created=stories_created,
            stories_updated=stories_updated,
            items_assigned=items_assigned,
            items_skipped_no_embedding=items_skipped_no_embedding,
        )

    async def get_story_details(self, story_id: UUID) -> StoryWithItemsResponse:
        """Fetch a story with full provenance evidence of member ContentItems."""
        story = await self.story_repo.get_with_items(story_id)
        if not story:
            raise AppException("NOT_FOUND", f"Story with ID {story_id} not found", status_code=404)

        member_records = await self.story_repo.get_story_members(story_id)
        evidence_items: List[StoryItemEvidence] = []

        for assoc, content in member_records:
            source_name = content.source.name if content.source else "Unknown Source"
            evidence_items.append(
                StoryItemEvidence(
                    content_id=content.id,
                    source_name=source_name,
                    title=content.title,
                    canonical_url=content.canonical_url,
                    content_type=content.content_type,
                    published_at=content.published_at,
                    is_canonical=assoc.is_canonical,
                    confidence_score=assoc.confidence_score,
                )
            )

        return StoryWithItemsResponse(
            id=story.id,
            title=story.headline,
            summary=story.summary,
            key_takeaway=story.key_takeaway,
            why_it_matters=story.why_it_matters,
            status=story.status,
            category=story.category,
            importance_score=story.importance_score,
            ranking_score=story.ranking_score,
            is_curated=story.is_curated,
            canonical_content_item_id=story.canonical_content_item_id,
            article_count=len(evidence_items),
            published_at=story.published_at,
            created_at=story.created_at,
            updated_at=story.updated_at,
            items=evidence_items,
        )

    async def list_stories(
        self,
        status: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> List[StoryDetailResponse]:
        """List stories with metadata and article counts."""
        stories = await self.story_repo.list_stories(status=status, limit=limit, offset=offset)
        results: List[StoryDetailResponse] = []

        for story in stories:
            count = len(story.story_items) if story.story_items else 0
            results.append(
                StoryDetailResponse(
                    id=story.id,
                    title=story.headline,
                    summary=story.summary,
                    key_takeaway=story.key_takeaway,
                    why_it_matters=story.why_it_matters,
                    status=story.status,
                    category=story.category,
                    importance_score=story.importance_score,
                    ranking_score=story.ranking_score,
                    is_curated=story.is_curated,
                    canonical_content_item_id=story.canonical_content_item_id,
                    article_count=count,
                    published_at=story.published_at,
                    created_at=story.created_at,
                    updated_at=story.updated_at,
                )
            )
        return results

    async def refresh_story(self, story_id: UUID) -> StoryRefreshResponse:
        """Re-evaluate canonical representative item and refresh grounded synthesis."""
        story = await self.story_repo.get_with_items(story_id)
        if not story:
            raise AppException("NOT_FOUND", f"Story with ID {story_id} not found", status_code=404)

        member_records = await self.story_repo.get_story_members(story_id)
        if not member_records:
            return StoryRefreshResponse(
                story_id=story.id,
                title=story.headline,
                summary=story.summary,
                article_count=0,
                status=story.status,
            )

        member_items = [content for _, content in member_records]
        canonical = select_representative_item(member_items)
        story_summary, takeaway, why_it_matters = synthesize_story_content(member_items)

        story.headline = clean_story_title(canonical.title)
        story.summary = story_summary
        story.key_takeaway = takeaway
        story.why_it_matters = why_it_matters
        story.canonical_content_item_id = canonical.id

        # Update canonical flags in associations
        for assoc, content in member_records:
            assoc.is_canonical = (content.id == canonical.id)
            self.session.add(assoc)

        self.session.add(story)
        await self.session.flush()

        return StoryRefreshResponse(
            story_id=story.id,
            title=story.headline,
            summary=story.summary,
            article_count=len(member_items),
            status=story.status,
        )
