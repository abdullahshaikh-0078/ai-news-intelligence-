from typing import List, Optional, Tuple
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.duplicate_pair import ContentDuplicatePair
from app.infrastructure.repositories.base import BaseRepository


class ContentDuplicateRepository(BaseRepository[ContentDuplicatePair]):
    """Repository managing duplicate relationships and pgvector similarity queries."""

    def __init__(self, session: AsyncSession):
        super().__init__(ContentDuplicatePair, session)

    async def find_similar_by_embedding(
        self,
        target_item_id: UUID,
        embedding: List[float],
        limit: int = 10,
        max_distance: Optional[float] = None,
    ) -> List[Tuple[ContentItem, float, float]]:
        """
        Database-side pgvector similarity query.
        Returns tuples of (ContentItem, distance, similarity) ordered by cosine distance ascending.
        Never loads embeddings into Python; all computation is executed in PostgreSQL.
        """
        dist_expr = ContentItem.embedding.cosine_distance(embedding)
        sim_expr = (1.0 - dist_expr).label("similarity")

        conditions = [
            ContentItem.id != target_item_id,
            ContentItem.embedding.is_not(None),
        ]
        if max_distance is not None:
            conditions.append(dist_expr <= max_distance)

        stmt = (
            select(ContentItem, dist_expr.label("distance"), sim_expr)
            .where(*conditions)
            .order_by(dist_expr.asc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return [(row[0], float(row[1]), float(row[2])) for row in result.all()]

    async def find_deterministic_matches(
        self,
        item: ContentItem,
    ) -> List[Tuple[ContentItem, str]]:
        """
        Identifies exact/near-exact duplicates using deterministic mechanisms:
        canonical_url, content_hash, and source-scoped external_id.
        """
        matches: List[Tuple[ContentItem, str]] = []
        seen_ids = set()

        # 1. Match by canonical URL
        if item.canonical_url:
            res_url = await self.session.execute(
                select(ContentItem).where(
                    ContentItem.id != item.id,
                    ContentItem.canonical_url == item.canonical_url,
                )
            )
            for matched in res_url.scalars().all():
                if matched.id not in seen_ids:
                    seen_ids.add(matched.id)
                    matches.append((matched, "CANONICAL_URL"))

        # 2. Match by exact content hash
        if item.content_hash:
            res_hash = await self.session.execute(
                select(ContentItem).where(
                    ContentItem.id != item.id,
                    ContentItem.content_hash == item.content_hash,
                )
            )
            for matched in res_hash.scalars().all():
                if matched.id not in seen_ids:
                    seen_ids.add(matched.id)
                    matches.append((matched, "CONTENT_HASH"))

        # 3. Match by source-scoped external ID
        if item.external_id:
            res_ext = await self.session.execute(
                select(ContentItem).where(
                    ContentItem.id != item.id,
                    ContentItem.source_id == item.source_id,
                    ContentItem.external_id == item.external_id,
                )
            )
            for matched in res_ext.scalars().all():
                if matched.id not in seen_ids:
                    seen_ids.add(matched.id)
                    matches.append((matched, "EXTERNAL_ID"))

        return matches

    async def upsert_pair(
        self,
        content_item_id: UUID,
        duplicate_content_item_id: UUID,
        similarity_score: float,
        cosine_distance: Optional[float],
        classification: str,
        detection_method: str,
        metadata_json: Optional[dict] = None,
    ) -> ContentDuplicatePair:
        """Idempotently insert or update an identified duplicate pair."""
        stmt = select(ContentDuplicatePair).where(
            ContentDuplicatePair.content_item_id == content_item_id,
            ContentDuplicatePair.duplicate_content_item_id == duplicate_content_item_id,
        )
        result = await self.session.execute(stmt)
        existing = result.scalars().first()

        if existing:
            existing.similarity_score = similarity_score
            existing.cosine_distance = cosine_distance
            existing.classification = classification
            existing.detection_method = detection_method
            existing.metadata_json = metadata_json or {}
            self.session.add(existing)
            await self.session.flush()
            return existing

        new_pair = ContentDuplicatePair(
            content_item_id=content_item_id,
            duplicate_content_item_id=duplicate_content_item_id,
            similarity_score=similarity_score,
            cosine_distance=cosine_distance,
            classification=classification,
            detection_method=detection_method,
            metadata_json=metadata_json or {},
        )
        self.session.add(new_pair)
        await self.session.flush()
        return new_pair

    async def list_for_item(self, content_id: UUID) -> List[ContentDuplicatePair]:
        """Retrieve all recorded duplicate pairs for a given content item."""
        stmt = (
            select(ContentDuplicatePair)
            .where(ContentDuplicatePair.content_item_id == content_id)
            .order_by(ContentDuplicatePair.similarity_score.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_embedded_candidates(self, limit: int = 50) -> List[ContentItem]:
        """Fetch a bounded batch of ContentItems that possess non-null embeddings."""
        stmt = (
            select(ContentItem)
            .where(ContentItem.embedding.is_not(None))
            .order_by(ContentItem.published_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
