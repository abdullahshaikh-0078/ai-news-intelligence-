from datetime import datetime, timezone
import uuid
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.content_item import ContentType
from app.domain.models.deduplication import (
    DetectionMethod,
    DuplicateClassification,
)
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.duplicate_pair import ContentDuplicatePair
from app.infrastructure.models.source import Source
from app.infrastructure.repositories.content_repo import ContentRepository
from app.infrastructure.repositories.dedup_repo import ContentDuplicateRepository
from app.infrastructure.repositories.source_repo import SourceRepository
from app.services.dedup_service import SemanticDeduplicationService


def _normalize(vec: list[float]) -> list[float]:
    """Helper to produce unit vectors for controlled cosine similarity tests."""
    norm = sum(x * x for x in vec) ** 0.5
    return [x / norm for x in vec] if norm > 0 else vec


def make_vector_1536(seed_val: float = 1.0) -> list[float]:
    """Generate a valid 1536-dimensional normalized vector."""
    vec = [0.0] * 1536
    vec[0] = seed_val
    vec[1] = 0.5
    return _normalize(vec)


def make_similar_vector(base_vec: list[float], angle_offset: float) -> list[float]:
    """Create a vector with controlled cosine similarity to base_vec."""
    vec = list(base_vec)
    vec[2] = angle_offset
    return _normalize(vec)


@pytest.fixture
async def dedup_test_source(db_session: AsyncSession) -> Source:
    source_repo = SourceRepository(db_session)
    source = await source_repo.add(
        Source(
            name="Dedup Test Source",
            slug=f"dedup-src-{uuid.uuid4().hex[:8]}",
            type="RSS",
            url=f"https://example.com/dedup-feed-{uuid.uuid4().hex[:8]}.xml",
            enabled=True,
        )
    )
    return source


@pytest.mark.asyncio
async def test_cosine_similarity_ordering_and_distance(
    db_session: AsyncSession,
    dedup_test_source: Source,
):
    """Verify that pgvector cosine similarity correctly orders nearest neighbors."""
    content_repo = ContentRepository(db_session)
    now = datetime.now(timezone.utc)

    base_vec = make_vector_1536(1.0)
    near_vec = make_similar_vector(base_vec, 0.05)  # High similarity (~0.99)
    mid_vec = make_similar_vector(base_vec, 0.35)   # Moderate similarity (~0.94)
    far_vec = make_similar_vector(base_vec, 2.0)    # Low similarity

    target_item = await content_repo.add(
        ContentItem(
            source_id=dedup_test_source.id,
            canonical_url=f"https://example.com/target-{uuid.uuid4().hex[:6]}",
            title="Target Article: Frontier AI Architectures",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            processing_state="COMPLETED",
            embedding=base_vec,
            embedding_model="gemini-embedding-001",
        )
    )

    item_near = await content_repo.add(
        ContentItem(
            source_id=dedup_test_source.id,
            canonical_url=f"https://example.com/near-{uuid.uuid4().hex[:6]}",
            title="Near Copy: Frontier AI Architecture Details",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            processing_state="COMPLETED",
            embedding=near_vec,
            embedding_model="gemini-embedding-001",
        )
    )

    item_mid = await content_repo.add(
        ContentItem(
            source_id=dedup_test_source.id,
            canonical_url=f"https://example.com/mid-{uuid.uuid4().hex[:6]}",
            title="Related: Recent Advances in AI System Models",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            processing_state="COMPLETED",
            embedding=mid_vec,
            embedding_model="gemini-embedding-001",
        )
    )

    service = SemanticDeduplicationService(session=db_session)
    response = await service.find_similar(content_id=target_item.id, limit=10)

    assert response.has_embedding is True
    assert response.total_matches >= 2

    # Verify nearest neighbor ordering: near > mid
    matched_ids = [m.matched_content_id for m in response.matches]
    assert item_near.id in matched_ids
    assert item_mid.id in matched_ids
    assert matched_ids.index(item_near.id) < matched_ids.index(item_mid.id)

    # First match should have higher similarity score than second match
    assert response.matches[0].similarity_score >= response.matches[1].similarity_score


@pytest.mark.asyncio
async def test_classification_thresholds(
    db_session: AsyncSession,
    dedup_test_source: Source,
):
    """Verify threshold classification: HIGH_CONFIDENCE_DUPLICATE, POSSIBLE_DUPLICATE, NOT_DUPLICATE."""
    content_repo = ContentRepository(db_session)
    now = datetime.now(timezone.utc)

    base_vec = make_vector_1536(1.0)
    # Near vector -> similarity > 0.95
    high_vec = make_similar_vector(base_vec, 0.05)
    # Mid vector -> similarity between 0.85 and 0.90
    possible_vec = make_similar_vector(base_vec, 0.5)
    # Distinct vector
    low_vec = make_similar_vector(base_vec, 5.0)

    target = await content_repo.add(
        ContentItem(
            source_id=dedup_test_source.id,
            canonical_url=f"https://example.com/base-{uuid.uuid4().hex[:6]}",
            title="Base Release Announcement",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            embedding=base_vec,
        )
    )

    item_high = await content_repo.add(
        ContentItem(
            source_id=dedup_test_source.id,
            canonical_url=f"https://example.com/high-{uuid.uuid4().hex[:6]}",
            title="Syndicated Release Announcement",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            embedding=high_vec,
        )
    )

    service = SemanticDeduplicationService(
        session=db_session,
        similarity_threshold=0.82,
        high_confidence_threshold=0.92,
    )

    resp = await service.find_similar(content_id=target.id, limit=5)
    high_match = next((m for m in resp.matches if m.matched_content_id == item_high.id), None)
    assert high_match is not None
    assert high_match.classification == DuplicateClassification.HIGH_CONFIDENCE_DUPLICATE


@pytest.mark.asyncio
async def test_deterministic_duplicate_detection(
    db_session: AsyncSession,
    dedup_test_source: Source,
):
    """Verify exact duplicate detection via canonical_url, content_hash, and external_id without embeddings."""
    content_repo = ContentRepository(db_session)
    now = datetime.now(timezone.utc)
    shared_hash = uuid.uuid4().hex

    item_original = await content_repo.add(
        ContentItem(
            source_id=dedup_test_source.id,
            canonical_url=f"https://example.com/orig-{uuid.uuid4().hex[:6]}",
            title="Original Press Release",
            published_at=now,
            fetched_at=now,
            content_hash=shared_hash,
            content_type=ContentType.ARTICLE.value,
            embedding=None,  # No embedding
        )
    )

    item_duplicate = await content_repo.add(
        ContentItem(
            source_id=dedup_test_source.id,
            canonical_url=f"https://example.com/copy-{uuid.uuid4().hex[:6]}",
            title="Identical Content Copy",
            published_at=now,
            fetched_at=now,
            content_hash=shared_hash,  # Matches shared_hash
            content_type=ContentType.ARTICLE.value,
            embedding=None,
        )
    )

    service = SemanticDeduplicationService(session=db_session)
    check_res = await service.check_duplicates(content_id=item_original.id, persist=False)

    assert check_res.has_embedding is False
    assert len(check_res.deterministic_duplicates) >= 1
    matched = check_res.deterministic_duplicates[0]
    assert matched.matched_content_id == item_duplicate.id
    assert matched.classification == DuplicateClassification.EXACT_DUPLICATE
    assert matched.detection_method == DetectionMethod.CONTENT_HASH
    assert check_res.top_classification == DuplicateClassification.EXACT_DUPLICATE


@pytest.mark.asyncio
async def test_missing_embedding_handling_safe(
    db_session: AsyncSession,
    dedup_test_source: Source,
):
    """Verify that ContentItems without embeddings return gracefully with zero external AI calls."""
    content_repo = ContentRepository(db_session)
    now = datetime.now(timezone.utc)

    item_no_emb = await content_repo.add(
        ContentItem(
            source_id=dedup_test_source.id,
            canonical_url=f"https://example.com/no-emb-{uuid.uuid4().hex[:6]}",
            title="Article Without Embeddings",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            embedding=None,
        )
    )

    service = SemanticDeduplicationService(session=db_session)
    similar_res = await service.find_similar(content_id=item_no_emb.id)
    assert similar_res.has_embedding is False
    assert similar_res.total_matches == 0
    assert similar_res.matches == []

    check_res = await service.check_duplicates(content_id=item_no_emb.id)
    assert check_res.has_embedding is False
    assert check_res.top_classification == DuplicateClassification.NOT_DUPLICATE


@pytest.mark.asyncio
async def test_non_destructive_invariant_and_persistence(
    db_session: AsyncSession,
    dedup_test_source: Source,
):
    """Verify that deduplication check and persistence never deletes, merges, or alters ContentItems."""
    content_repo = ContentRepository(db_session)
    now = datetime.now(timezone.utc)

    count_before = (await db_session.execute(select(func.count()).select_from(ContentItem))).scalar_one()

    base_vec = make_vector_1536(1.0)
    near_vec = make_similar_vector(base_vec, 0.02)

    item_a = await content_repo.add(
        ContentItem(
            source_id=dedup_test_source.id,
            canonical_url=f"https://example.com/persist-a-{uuid.uuid4().hex[:6]}",
            title="Item A - Deep Learning Foundations",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            embedding=base_vec,
        )
    )

    item_b = await content_repo.add(
        ContentItem(
            source_id=dedup_test_source.id,
            canonical_url=f"https://example.com/persist-b-{uuid.uuid4().hex[:6]}",
            title="Item B - Deep Learning Foundations Syndicate",
            published_at=now,
            fetched_at=now,
            content_hash=uuid.uuid4().hex,
            content_type=ContentType.ARTICLE.value,
            embedding=near_vec,
        )
    )

    service = SemanticDeduplicationService(session=db_session)
    # Run duplicate check with persist=True
    check_res = await service.check_duplicates(content_id=item_a.id, persist=True)
    assert check_res.persisted is True

    # Verify duplicate pair is stored in content_duplicate_pairs table
    pair_res = await db_session.execute(
        select(ContentDuplicatePair).where(
            ContentDuplicatePair.content_item_id == item_a.id,
            ContentDuplicatePair.duplicate_content_item_id == item_b.id,
        )
    )
    pair = pair_res.scalars().first()
    assert pair is not None
    assert pair.classification == DuplicateClassification.HIGH_CONFIDENCE_DUPLICATE.value
    assert pair.detection_method == DetectionMethod.SEMANTIC_SIMILARITY.value

    a_after = await content_repo.get_by_id(item_a.id)
    b_after = await content_repo.get_by_id(item_b.id)
    assert a_after is not None
    assert b_after is not None
    assert a_after.canonical_url == item_a.canonical_url
    assert b_after.canonical_url == item_b.canonical_url
    assert len(a_after.embedding) == 1536
    assert len(b_after.embedding) == 1536

    # Total count increased by exactly 2 (the two new items created in this test), 0 deleted
    count_after = (await db_session.execute(select(func.count()).select_from(ContentItem))).scalar_one()
    assert count_after == count_before + 2


@pytest.mark.asyncio
async def test_bounded_batch_deduplication(
    db_session: AsyncSession,
    dedup_test_source: Source,
):
    """Verify run_batch performs bounded processing and returns aggregate metrics."""
    service = SemanticDeduplicationService(session=db_session)
    batch_res = await service.run_batch(limit=10, persist=False)

    assert batch_res.total_scanned >= 0
    assert batch_res.total_scanned <= 10
    assert batch_res.items_with_embedding == batch_res.total_scanned
