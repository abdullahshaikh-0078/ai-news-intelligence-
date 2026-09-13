import asyncio
import os
import sys
from uuid import UUID

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from sqlalchemy import func, select, text
from app.infrastructure.database.session import AsyncSessionLocal
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.duplicate_pair import ContentDuplicatePair
from app.services.dedup_service import SemanticDeduplicationService
from app.core.config import settings
import httpx


async def verify_phase10_live():
    print("=" * 75)
    print("PHASE 10 — REAL DATABASE SEMANTIC DEDUPLICATION VERIFICATION")
    print("=" * 75)

    async with AsyncSessionLocal() as session:
        # 1. Baseline State Checks
        count_stmt = select(func.count()).select_from(ContentItem)
        total_items_before = (await session.execute(count_stmt)).scalar_one()

        emb_stmt = select(func.count()).select_from(ContentItem).where(ContentItem.embedding.is_not(None))
        total_embeddings_before = (await session.execute(emb_stmt)).scalar_one()

        rev_stmt = text("SELECT version_num FROM alembic_version")
        alembic_head = (await session.execute(rev_stmt)).scalar_one()

        print("\n[1] Initial Database Baseline:")
        print(f"    Total ContentItems:       {total_items_before} (Expected 1348)")
        print(f"    Total Non-null Embeddings:{total_embeddings_before} (Expected >= 4)")
        print(f"    Alembic Revision:         {alembic_head} (Expected b4de7a8912c3)")
        print(f"    Similarity Threshold:     {settings.SEMANTIC_DEDUP_SIMILARITY_THRESHOLD}")
        print(f"    High Conf. Threshold:     {settings.SEMANTIC_DEDUP_HIGH_CONFIDENCE_THRESHOLD}")

        assert total_items_before == 1348, f"Expected 1348 items, found {total_items_before}"
        assert total_embeddings_before >= 4, f"Expected at least 4 embeddings, found {total_embeddings_before}"
        assert alembic_head == "b4de7a8912c3", f"Expected revision b4de7a8912c3, found {alembic_head}"

        # 2. Inspect Existing Embedded Items
        embedded_items = (
            await session.execute(
                select(ContentItem)
                .where(ContentItem.embedding.is_not(None))
                .order_by(ContentItem.published_at.desc())
            )
        ).scalars().all()

        print("\n[2] Existing Embedded ContentItems:")
        for idx, itm in enumerate(embedded_items, 1):
            dims = len(itm.embedding) if itm.embedding else 0
            print(f"    {idx}. [{itm.content_type:<14}] ({itm.id}) {itm.title[:45]}... (dims={dims})")

        # 3. Test Native pgvector Cosine Distance (<=>) & Nearest Neighbor Ordering
        target = embedded_items[0]
        print(f"\n[3] Running Nearest Neighbor Similarity Search for Target Item:")
        print(f"    Target: [{target.content_type}] '{target.title[:50]}'")

        service = SemanticDeduplicationService(session=session)
        similar_resp = await service.find_similar(content_id=target.id, limit=5)

        print(f"    Has Embedding: {similar_resp.has_embedding}")
        print(f"    Total Matches Found: {similar_resp.total_matches}")
        for idx, match in enumerate(similar_resp.matches, 1):
            print(
                f"      Match {idx}: Sim={match.similarity_score:.4f} | "
                f"Dist={match.cosine_distance:.4f} | "
                f"Class={match.classification:<24} | "
                f"Title='{match.matched_title[:35]}'"
            )

        assert similar_resp.has_embedding is True
        assert similar_resp.total_matches > 0
        # Assert descending similarity ordering
        scores = [m.similarity_score for m in similar_resp.matches]
        assert scores == sorted(scores, reverse=True), "Matches must be strictly sorted by descending similarity"

        # 4. Test Single-Item Deduplication Check & Non-Destructive Persistence
        print(f"\n[4] Testing Deduplication Check with Persistence:")
        check_resp = await service.check_duplicates(content_id=target.id, persist=True)
        await session.commit()

        print(f"    Target Item ID:       {check_resp.content_id}")
        print(f"    Deterministic Matches:{len(check_resp.deterministic_duplicates)}")
        print(f"    Semantic Matches:     {len(check_resp.semantic_duplicates)}")
        print(f"    Top Classification:   {check_resp.top_classification}")
        print(f"    Persisted:            {check_resp.persisted}")

        # 5. Test Bounded Batch Deduplication
        print(f"\n[5] Running Bounded Batch Deduplication across Embedded Items:")
        batch_resp = await service.run_batch(limit=10, persist=True)
        await session.commit()

        print(f"    Total Scanned:         {batch_resp.total_scanned}")
        print(f"    Items With Embedding:  {batch_resp.items_with_embedding}")
        print(f"    Duplicate Pairs Found: {batch_resp.duplicate_pairs_identified}")
        print(f"    Pairs Persisted:       {batch_resp.persisted_pairs_count}")

        # 6. Verify ContentDuplicatePair Storage
        pairs_count = (await session.execute(select(func.count()).select_from(ContentDuplicatePair))).scalar_one()
        print(f"\n[6] Database ContentDuplicatePair Count: {pairs_count}")

        # 7. Test HTTP API Endpoints via Uvicorn Daemon
        print(f"\n[7] Verifying Live HTTP Endpoints on http://127.0.0.1:8000:")
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8000") as client:
            resp_sim = await client.get(f"/api/v1/dedup/similar/{target.id}?limit=3")
            assert resp_sim.status_code == 200, f"GET /similar returned {resp_sim.status_code}: {resp_sim.text}"
            sim_data = resp_sim.json()
            print(f"    GET  /api/v1/dedup/similar/{target.id} -> 200 OK (Matches: {len(sim_data['matches'])})")

            resp_chk = await client.post(f"/api/v1/dedup/check/{target.id}?persist=false")
            assert resp_chk.status_code == 200, f"POST /check returned {resp_chk.status_code}: {resp_chk.text}"
            chk_data = resp_chk.json()
            print(f"    POST /api/v1/dedup/check/{target.id}   -> 200 OK (Top Class: {chk_data['top_classification']})")

            resp_run = await client.post("/api/v1/dedup/run", json={"limit": 5, "persist": False})
            assert resp_run.status_code == 200, f"POST /run returned {resp_run.status_code}: {resp_run.text}"
            run_data = resp_run.json()
            print(f"    POST /api/v1/dedup/run                 -> 200 OK (Scanned: {run_data['total_scanned']})")

        # 8. Strict Non-Destructive Integrity Verification
        total_items_after = (await session.execute(count_stmt)).scalar_one()
        total_embeddings_after = (await session.execute(emb_stmt)).scalar_one()

        print(f"\n[8] Final Database Integrity Verification:")
        print(f"    ContentItems Before: {total_items_before} | After: {total_items_after} (Difference: {total_items_after - total_items_before})")
        print(f"    Embeddings Before:   {total_embeddings_before} | After: {total_embeddings_after} (Difference: {total_embeddings_after - total_embeddings_before})")
        print(f"    ContentItems Deleted: 0")
        print(f"    ContentItems Merged:  0")

        assert total_items_before == total_items_after == 1348, "ContentItem count changed! Non-destructive violation!"
        assert total_embeddings_before == total_embeddings_after, "Embedding count changed! Non-destructive violation!"

    print("\n" + "=" * 75)
    print("PHASE 10 LIVE VERIFICATION SUCCESSFUL!")
    print("=" * 75)


if __name__ == "__main__":
    asyncio.run(verify_phase10_live())
