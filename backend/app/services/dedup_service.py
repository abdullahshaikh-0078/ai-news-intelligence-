from typing import List, Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import AppException
from app.core.logging import logger
from app.domain.models.deduplication import (
    BatchDeduplicationResponse,
    ContentSimilarResponse,
    DetectionMethod,
    DuplicateCheckResponse,
    DuplicateClassification,
    DuplicateMatch,
)
from app.infrastructure.repositories.content_repo import ContentRepository
from app.infrastructure.repositories.dedup_repo import ContentDuplicateRepository


class SemanticDeduplicationService:
    """
    Dedicated service for semantic and deterministic duplicate detection.
    Enforces non-destructive evaluation: IDENTIFY + RECORD, never DELETE + MERGE.
    """

    def __init__(
        self,
        session: AsyncSession,
        similarity_threshold: Optional[float] = None,
        high_confidence_threshold: Optional[float] = None,
    ):
        self.session = session
        self.content_repo = ContentRepository(session)
        self.dedup_repo = ContentDuplicateRepository(session)
        self.similarity_threshold = (
            similarity_threshold
            if similarity_threshold is not None
            else settings.SEMANTIC_DEDUP_SIMILARITY_THRESHOLD
        )
        self.high_confidence_threshold = (
            high_confidence_threshold
            if high_confidence_threshold is not None
            else settings.SEMANTIC_DEDUP_HIGH_CONFIDENCE_THRESHOLD
        )

    def _classify_similarity(self, similarity: float) -> DuplicateClassification:
        """Categorize similarity score against configured thresholds."""
        if similarity >= self.high_confidence_threshold:
            return DuplicateClassification.HIGH_CONFIDENCE_DUPLICATE
        elif similarity >= self.similarity_threshold:
            return DuplicateClassification.POSSIBLE_DUPLICATE
        return DuplicateClassification.NOT_DUPLICATE

    async def find_similar(
        self,
        content_id: UUID,
        limit: int = 10,
        min_similarity: Optional[float] = None,
    ) -> ContentSimilarResponse:
        """
        Query nearest neighbors by pgvector cosine similarity without external AI calls.
        Safe against items lacking embeddings.
        """
        item = await self.content_repo.get_by_id(content_id)
        if not item:
            raise AppException("NOT_FOUND", f"ContentItem with ID {content_id} not found", status_code=404)

        if not item.embedding:
            logger.info(f"[SemanticDeduplicationService] ContentItem {content_id} has no embedding; skipping vector search")
            return ContentSimilarResponse(
                content_id=content_id,
                has_embedding=False,
                total_matches=0,
                matches=[],
            )

        max_dist = (1.0 - min_similarity) if min_similarity is not None else None
        bounded_limit = min(max(1, limit), 50)

        similar_tuples = await self.dedup_repo.find_similar_by_embedding(
            target_item_id=content_id,
            embedding=item.embedding,
            limit=bounded_limit,
            max_distance=max_dist,
        )

        matches: List[DuplicateMatch] = []
        for matched_item, dist, sim in similar_tuples:
            classification = self._classify_similarity(sim)
            matches.append(
                DuplicateMatch(
                    target_content_id=content_id,
                    matched_content_id=matched_item.id,
                    matched_title=matched_item.title,
                    matched_url=matched_item.canonical_url,
                    similarity_score=round(sim, 4),
                    cosine_distance=round(dist, 4),
                    classification=classification,
                    detection_method=DetectionMethod.SEMANTIC_SIMILARITY,
                    metadata_json={
                        "embedding_model": matched_item.embedding_model,
                        "source_id": str(matched_item.source_id),
                    },
                )
            )

        return ContentSimilarResponse(
            content_id=content_id,
            has_embedding=True,
            total_matches=len(matches),
            matches=matches,
        )

    async def check_duplicates(
        self,
        content_id: UUID,
        persist: bool = False,
    ) -> DuplicateCheckResponse:
        """
        Comprehensive duplicate inspection combining deterministic and semantic mechanisms.
        Never mutates or deletes content records.
        """
        item = await self.content_repo.get_by_id(content_id)
        if not item:
            raise AppException("NOT_FOUND", f"ContentItem with ID {content_id} not found", status_code=404)

        # 1. Deterministic Identity Matches
        deterministic_raw = await self.dedup_repo.find_deterministic_matches(item)
        deterministic_matches: List[DuplicateMatch] = []
        seen_matched_ids = set()

        for matched_item, method_str in deterministic_raw:
            seen_matched_ids.add(matched_item.id)
            match_obj = DuplicateMatch(
                target_content_id=content_id,
                matched_content_id=matched_item.id,
                matched_title=matched_item.title,
                matched_url=matched_item.canonical_url,
                similarity_score=1.0,
                cosine_distance=0.0,
                classification=DuplicateClassification.EXACT_DUPLICATE,
                detection_method=DetectionMethod(method_str),
                metadata_json={"source_id": str(matched_item.source_id)},
            )
            deterministic_matches.append(match_obj)

            if persist:
                await self.dedup_repo.upsert_pair(
                    content_item_id=content_id,
                    duplicate_content_item_id=matched_item.id,
                    similarity_score=1.0,
                    cosine_distance=0.0,
                    classification=DuplicateClassification.EXACT_DUPLICATE.value,
                    detection_method=method_str,
                    metadata_json={"source_id": str(matched_item.source_id)},
                )

        # 2. Semantic Similarity Matches
        semantic_matches: List[DuplicateMatch] = []
        has_embedding = bool(item.embedding)

        if has_embedding:
            # Query top nearest neighbors
            similar_tuples = await self.dedup_repo.find_similar_by_embedding(
                target_item_id=content_id,
                embedding=item.embedding,
                limit=20,
            )

            for matched_item, dist, sim in similar_tuples:
                if matched_item.id in seen_matched_ids:
                    continue  # Already captured as exact deterministic duplicate

                classification = self._classify_similarity(sim)
                if classification != DuplicateClassification.NOT_DUPLICATE:
                    match_obj = DuplicateMatch(
                        target_content_id=content_id,
                        matched_content_id=matched_item.id,
                        matched_title=matched_item.title,
                        matched_url=matched_item.canonical_url,
                        similarity_score=round(sim, 4),
                        cosine_distance=round(dist, 4),
                        classification=classification,
                        detection_method=DetectionMethod.SEMANTIC_SIMILARITY,
                        metadata_json={
                            "embedding_model": matched_item.embedding_model,
                            "source_id": str(matched_item.source_id),
                        },
                    )
                    semantic_matches.append(match_obj)

                    if persist:
                        await self.dedup_repo.upsert_pair(
                            content_item_id=content_id,
                            duplicate_content_item_id=matched_item.id,
                            similarity_score=round(sim, 4),
                            cosine_distance=round(dist, 4),
                            classification=classification.value,
                            detection_method=DetectionMethod.SEMANTIC_SIMILARITY.value,
                            metadata_json={
                                "embedding_model": matched_item.embedding_model,
                                "source_id": str(matched_item.source_id),
                            },
                        )

        # Determine top aggregate classification
        if deterministic_matches:
            top_classification = DuplicateClassification.EXACT_DUPLICATE
        elif any(m.classification == DuplicateClassification.HIGH_CONFIDENCE_DUPLICATE for m in semantic_matches):
            top_classification = DuplicateClassification.HIGH_CONFIDENCE_DUPLICATE
        elif any(m.classification == DuplicateClassification.POSSIBLE_DUPLICATE for m in semantic_matches):
            top_classification = DuplicateClassification.POSSIBLE_DUPLICATE
        else:
            top_classification = DuplicateClassification.NOT_DUPLICATE

        return DuplicateCheckResponse(
            content_id=content_id,
            has_embedding=has_embedding,
            deterministic_duplicates=deterministic_matches,
            semantic_duplicates=semantic_matches,
            top_classification=top_classification,
            persisted=persist,
        )

    async def run_batch(
        self,
        limit: int = 50,
        persist: bool = True,
    ) -> BatchDeduplicationResponse:
        """
        Execute bounded batch deduplication on candidates with non-null embeddings.
        Maintains bounded execution to protect resources.
        """
        bounded_limit = min(max(1, limit), 100)
        candidates = await self.dedup_repo.list_embedded_candidates(limit=bounded_limit)

        total_scanned = len(candidates)
        items_with_embedding = total_scanned
        items_skipped_no_embedding = 0
        duplicate_pairs_identified = 0
        persisted_pairs_count = 0

        for candidate in candidates:
            res = await self.check_duplicates(content_id=candidate.id, persist=persist)
            found_count = len(res.deterministic_duplicates) + len(res.semantic_duplicates)
            if found_count > 0:
                duplicate_pairs_identified += found_count
                if persist:
                    persisted_pairs_count += found_count

        return BatchDeduplicationResponse(
            total_scanned=total_scanned,
            items_with_embedding=items_with_embedding,
            items_skipped_no_embedding=items_skipped_no_embedding,
            duplicate_pairs_identified=duplicate_pairs_identified,
            persisted_pairs_count=persisted_pairs_count,
        )
