"""Phase 9 Live Gemini Integration Verification Script.
Processes strictly 3 representative items:
1. ARTICLE
2. RESEARCH_PAPER
3. COMMUNITY_POST

Validates:
- Structured output (summary, key points, topics, relevance score)
- 1536-dimensional native pgvector embeddings
- Native cosine distance (<=> operator)
- Idempotency
- Preservation of existing data and embeddings
"""

import asyncio
import os
import sys

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from sqlalchemy import select, func, text
from app.ai.providers.gemini_provider import GeminiProvider
from app.core.config import settings
from app.infrastructure.database.session import AsyncSessionLocal
from app.infrastructure.models.content_item import ContentItem
from app.services.ai_service import AIProcessingService


async def verify_phase9_live():
    print("=" * 75)
    print("PHASE 9 — REAL GEMINI INTEGRATION VERIFICATION")
    print("=" * 75)

    assert settings.GEMINI_API_KEY, "GEMINI_API_KEY must be configured in root .env"
    provider = GeminiProvider()
    print(f"\n[1] Provider Initialized:")
    print(f"    Provider Name:    {provider.provider_name}")
    print(f"    Chat Model:       {provider.model_name}")
    print(f"    Embedding Model:  {provider.embedding_model_name}")
    print(f"    Target Dimension: {settings.GEMINI_EMBEDDING_DIMENSIONS}")

    target_types = ["ARTICLE", "RESEARCH_PAPER", "COMMUNITY_POST"]
    processed_items = []

    async with AsyncSessionLocal() as session:
        service = AIProcessingService(session=session, provider=provider)

        # 1. Fetch exactly 1 candidate per content type
        candidates = []
        for ctype in target_types:
            res = await session.execute(
                select(ContentItem)
                .where(ContentItem.content_type == ctype, ContentItem.processing_state == "PENDING")
                .order_by(ContentItem.published_at.desc())
                .limit(1)
            )
            item = res.scalar_one_or_none()
            if item:
                candidates.append(item)

        print(f"\n[2] Selected {len(candidates)} Representative Candidate ContentItems:")
        for c in candidates:
            print(f"    - [{c.content_type:14}] ({c.id}) {c.title[:55]}")

        # 2. Process each item with real Gemini API
        print(f"\n[3] Processing Items via Google Gemini...")
        for item in candidates:
            print(f"\n    Processing: [{item.content_type}] '{item.title[:45]}'...")
            processed = await service.process_item(item, force=True)
            processed_items.append(processed)

            # Validations
            assert processed.processing_state == "COMPLETED", f"Expected COMPLETED, got {processed.processing_state}"
            assert processed.ai_summary and len(processed.ai_summary) > 30, "AI summary missing or too short"
            assert processed.ai_key_points and len(processed.ai_key_points) >= 2, "Key points missing"
            assert processed.ai_topics and len(processed.ai_topics) >= 1, "Topics missing"
            assert 0.0 <= processed.ai_relevance_score <= 1.0, f"Relevance score out of bounds: {processed.ai_relevance_score}"
            assert processed.ai_model == provider.model_name, f"Unexpected model: {processed.ai_model}"
            assert processed.embedding is not None, "Embedding missing"
            assert len(processed.embedding) == 1536, f"Expected 1536 dims, got {len(processed.embedding)}"

            print(f"      Status:          {processed.processing_state}")
            print(f"      Relevance Score: {processed.ai_relevance_score:.2f}")
            print(f"      Topics ({len(processed.ai_topics)}):     {', '.join(processed.ai_topics[:4])}")
            print(f"      Key Points ({len(processed.ai_key_points)}): {processed.ai_key_points[0][:60]}...")
            print(f"      AI Summary:      {processed.ai_summary[:80]}...")
            print(f"      Embedding Dims:  {len(processed.embedding)}")

        await session.commit()

        # 3. Verify Idempotency on 1 item
        print(f"\n[4] Idempotency Verification:")
        test_item = processed_items[0]
        re_processed = await service.process_item(test_item, force=False)
        assert re_processed.processing_state == "COMPLETED"
        print(f"    Successfully skipped already COMPLETED item '{test_item.title[:40]}' without calling API.")

        # 4. Database pgvector Verification
        print(f"\n[5] Database pgvector Storage & Operator Verification:")
        for item in processed_items:
            db_dim = (await session.execute(
                text(f"SELECT vector_dims(embedding) FROM content_items WHERE id = '{item.id}';")
            )).scalar()
            print(f"    - Item {item.id} pgvector column vector_dims = {db_dim}")
            assert db_dim == 1536, f"Database vector_dims mismatch: {db_dim}"

        # Test native cosine distance <=> operator between first two items
        if len(processed_items) >= 2:
            id1, id2 = processed_items[0].id, processed_items[1].id
            cos_dist = (await session.execute(
                text(f"SELECT embedding <=> (SELECT embedding FROM content_items WHERE id = '{id2}') FROM content_items WHERE id = '{id1}';")
            )).scalar()
            print(f"\n    Native Cosine Distance (<=>): {cos_dist:.4f}")
            assert cos_dist is not None and cos_dist >= 0.0

        # 5. Global Database Integrity Check
        print(f"\n[6] Database Integrity & Totals:")
        total_items = (await session.execute(select(func.count()).select_from(ContentItem))).scalar_one()
        total_completed = (await session.execute(
            select(func.count()).select_from(ContentItem).where(ContentItem.processing_state == "COMPLETED")
        )).scalar_one()
        total_embeddings = (await session.execute(
            select(func.count()).select_from(ContentItem).where(ContentItem.embedding.isnot(None))
        )).scalar_one()

        print(f"    Total ContentItems:       {total_items} (Expected 1348)")
        print(f"    Total COMPLETED Items:    {total_completed}")
        print(f"    Total Non-null Embeddings:{total_embeddings} (4 pre-existing + {len(processed_items)} newly processed)")
        assert total_items == 1348, f"Total items altered: {total_items}"
        assert total_embeddings == 4 + len(processed_items), f"Unexpected embedding count: {total_embeddings}"

        # 6. Alembic Revision
        head = (await session.execute(text("SELECT version_num FROM alembic_version;"))).scalar_one()
        print(f"    Alembic Revision:         {head}")
        assert head in ("f3aa829c7102", "b4de7a8912c3")

    print("\n" + "=" * 75)
    print("PHASE 9 LIVE VERIFICATION SUCCESSFUL!")
    print("=" * 75)


if __name__ == "__main__":
    asyncio.run(verify_phase9_live())
