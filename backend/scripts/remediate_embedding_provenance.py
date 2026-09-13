"""
Remediate Embedding Provenance Script.

Generates genuine Google Gemini embeddings (gemini-embedding-001, 1536-dim)
for the exact 4 currently embedded ContentItems in ai_news_intel.

Guarantees:
- Memory-first generation with rate-limit pacing (fails safely if Gemini 429/error).
- Single transaction database update.
- ContentItem count remains strictly 1,348.
- Embedded count remains strictly 4.
- All embedding_model identifiers become 'gemini-embedding-001'.
- Stories and memberships remain 100% preserved.
- Semantic verification and compatibility checks.
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import asyncpg
from app.ai.providers.gemini_provider import GeminiProvider
from app.core.config import settings

DB_URL = "postgresql://postgres:postgres@localhost:5433/ai_news_intel"


async def main():
    print("=" * 80)
    print("EMBEDDING PROVENANCE REMEDIATION — LIVE GEMINI PRODUCTION CORRECTION")
    print("=" * 80)

    # 1. Inspect and record baseline state
    print("\n[1/6] Recording Pre-Remediation Baseline Snapshot...")
    conn = await asyncpg.connect(DB_URL)
    try:
        total_items_before = await conn.fetchval("SELECT count(*) FROM content_items;")
        embedded_count_before = await conn.fetchval("SELECT count(*) FROM content_items WHERE embedding IS NOT NULL;")
        total_stories_before = await conn.fetchval("SELECT count(*) FROM stories;")
        total_memberships_before = await conn.fetchval("SELECT count(*) FROM story_content_items;")

        rows_before = await conn.fetch("""
            SELECT id, title, content_type, processing_state, embedding_model,
                   vector_dims(embedding) as dims, ai_relevance_score
            FROM content_items
            WHERE embedding IS NOT NULL
            ORDER BY created_at ASC;
        """)

        print(f"  - ContentItem Count:       {total_items_before} (Expected: 1,348)")
        print(f"  - Non-Null Embeddings:     {embedded_count_before} (Expected: 4)")
        print(f"  - Clustered Stories:       {total_stories_before} (Expected: 4)")
        print(f"  - Story Memberships:       {total_memberships_before} (Expected: 4)")
        print("  - Pre-remediation Items & Models:")
        for r in rows_before:
            print(f"    * {r['id']} | type={r['content_type']} | state={r['processing_state']} | model={r['embedding_model']} | dims={r['dims']} | '{r['title'][:40]}'")

        assert total_items_before == 1348, f"Unexpected items: {total_items_before}"
        assert embedded_count_before == 4, f"Unexpected embeddings: {embedded_count_before}"
        assert total_stories_before == 4, f"Unexpected stories: {total_stories_before}"
        assert total_memberships_before == 4, f"Unexpected memberships: {total_memberships_before}"

        target_ids = [r["id"] for r in rows_before]

        # 2. Fetch full item details for the 4 items
        items_data = await conn.fetch("""
            SELECT id, title, summary, raw_content, content_type, author, metadata_json
            FROM content_items
            WHERE id = ANY($1::uuid[])
            ORDER BY created_at ASC;
        """, target_ids)

    finally:
        await conn.close()

    # 3. Initialize Production Google Gemini Provider
    print("\n[2/6] Initializing Production Gemini Provider...")
    assert settings.GEMINI_API_KEY, "GEMINI_API_KEY must be configured in .env"
    provider = GeminiProvider(
        embedding_model="gemini-embedding-001",
        embedding_dimensions=1536,
    )
    print(f"  - Provider:         {provider.provider_name}")
    print(f"  - Embedding Model:  {provider.embedding_model_name}")
    print(f"  - Chat Model:       {provider.model_name}")
    print(f"  - Target Dimensions:{provider._embedding_dimensions}")

    # 4. Generate Genuine Gemini Embeddings & Analysis in Memory (Fail-Safe)
    print("\n[3/6] Generating Genuine Gemini Embeddings in Memory...")
    generated_payloads = {}

    for idx, item in enumerate(items_data, start=1):
        item_id = item["id"]
        title = item["title"].strip()
        summary = (item["summary"] or item["raw_content"] or "").strip()[:1000]
        embed_text = f"{title}\n{summary}" if summary else title

        print(f"  [{idx}/4] Requesting Gemini embedding for ({item['content_type']}) '{title[:45]}'...")
        t0 = time.perf_counter()
        try:
            vector = await provider.generate_embedding(embed_text)
            elapsed = time.perf_counter() - t0
            assert len(vector) == 1536, f"Expected 1536 dimensions, got {len(vector)}"
            print(f"        Success in {elapsed:.2f}s! Vector length: {len(vector)}, sample: {vector[:3]}")

            # For Item 1 (which had PENDING processing state and NULL summary), also generate Gemini analysis
            analysis = None
            if item_id == target_ids[0]:  # Item 1: Introducing Nano Banana Pro
                print("        Also generating Gemini AI analysis for Item 1...")
                analysis = await provider.analyze_content(
                    text=f"Title: {title}\nSummary: {summary}",
                    content_type=item["content_type"],
                    metadata=item["metadata_json"],
                )
                print(f"        AI Summary: {analysis.summary[:60]}... (Relevance: {analysis.relevance_score})")

            generated_payloads[item_id] = {
                "vector": vector,
                "vector_str": "[" + ",".join(str(v) for v in vector) + "]",
                "analysis": analysis,
            }

            # Pacing between Gemini API calls to prevent 429
            if idx < len(items_data):
                await asyncio.sleep(1.5)

        except Exception as exc:
            print(f"\n[FATAL] Gemini generation failed on item {item_id}: {exc}")
            print("ABORTING REMEDIATION SAFELY. Database was NOT modified.")
            sys.exit(1)

    assert len(generated_payloads) == 4, "Did not generate all 4 embeddings"
    print("  => All 4 genuine Gemini embeddings successfully generated in memory [PASS]")

    # 5. Transaction-Safe Database Remediation
    print("\n[4/6] Executing Transaction-Safe Database Remediation...")
    conn = await asyncpg.connect(DB_URL)
    now = datetime.now(timezone.utc)
    try:
        async with conn.transaction():
            for item_id, payload in generated_payloads.items():
                vector_str = payload["vector_str"]
                analysis = payload["analysis"]

                if analysis is not None:
                    # Update Item 1 with embedding AND AI intelligence
                    await conn.execute("""
                        UPDATE content_items
                        SET embedding = $1::vector,
                            embedding_model = 'gemini-embedding-001',
                            ai_summary = $2,
                            ai_key_points = $3::json,
                            ai_topics = $4::json,
                            ai_relevance_score = $5,
                            ai_model = $6,
                            ai_processed_at = $7,
                            processing_state = 'COMPLETED',
                            updated_at = $7
                        WHERE id = $8;
                    """,
                        vector_str,
                        analysis.summary,
                        json.dumps(analysis.key_points),
                        json.dumps(analysis.topics),
                        analysis.relevance_score,
                        provider.model_name,
                        now,
                        item_id,
                    )
                else:
                    # Update Items 2, 3, 4 with embedding and model identifier
                    await conn.execute("""
                        UPDATE content_items
                        SET embedding = $1::vector,
                            embedding_model = 'gemini-embedding-001',
                            updated_at = $2
                        WHERE id = $3;
                    """,
                        vector_str,
                        now,
                        item_id,
                    )

            print("  - Transaction committed successfully!")

        # 6. Post-Remediation Verification
        print("\n[5/6] Post-Remediation Database Verification...")
        total_items_after = await conn.fetchval("SELECT count(*) FROM content_items;")
        embedded_count_after = await conn.fetchval("SELECT count(*) FROM content_items WHERE embedding IS NOT NULL;")
        total_stories_after = await conn.fetchval("SELECT count(*) FROM stories;")
        total_memberships_after = await conn.fetchval("SELECT count(*) FROM story_content_items;")

        rows_after = await conn.fetch("""
            SELECT id, title, content_type, processing_state, embedding_model,
                   vector_dims(embedding) as dims, ai_relevance_score
            FROM content_items
            WHERE embedding IS NOT NULL
            ORDER BY created_at ASC;
        """)

        print(f"  - ContentItem Count (After):   {total_items_after} (Expected: 1,348)")
        print(f"  - Non-Null Embeddings (After): {embedded_count_after} (Expected: 4)")
        print(f"  - Clustered Stories (After):   {total_stories_after} (Expected: 4)")
        print(f"  - Story Memberships (After):   {total_memberships_after} (Expected: 4)")

        assert total_items_after == 1348, "Total items changed!"
        assert embedded_count_after == 4, "Embedding count changed!"
        assert total_stories_after == 4, "Stories count changed!"
        assert total_memberships_after == 4, "Memberships changed!"

        print("\n  Updated ContentItem Metadata in Database:")
        for r in rows_after:
            print(f"    * ID: {r['id']}")
            print(f"      Title:           {r['title'][:50]}")
            print(f"      Type:            {r['content_type']}")
            print(f"      State:           {r['processing_state']}")
            print(f"      Embedding Model: {r['embedding_model']} (dims={r['dims']})")
            print(f"      Relevance:       {r['ai_relevance_score']}")
            assert r['embedding_model'] == 'gemini-embedding-001', f"Incorrect model: {r['embedding_model']}"
            assert r['dims'] == 1536, f"Incorrect dimensions: {r['dims']}"

        # 7. Semantic & Cosine Operator Sanity Check
        print("\n[6/6] Semantic Sanity Check on Actual Gemini Embeddings...")

        # Self-distance
        self_dist = await conn.fetchval("SELECT (embedding <=> embedding) FROM content_items WHERE embedding IS NOT NULL LIMIT 1;")
        print(f"  - Self-Distance (<=> to self, expected 0.0): {self_dist:.6f}")
        assert abs(self_dist) < 1e-6, "Self distance not 0.0"

        # Pairwise distances
        pairs = await conn.fetch("""
            SELECT a.id as id_a, a.title as title_a,
                   b.id as id_b, b.title as title_b,
                   (a.embedding <=> b.embedding) as cos_dist
            FROM content_items a, content_items b
            WHERE a.embedding IS NOT NULL AND b.embedding IS NOT NULL AND a.id < b.id
            ORDER BY cos_dist ASC;
        """)

        print(f"  - Computed {len(pairs)} pairwise cosine distances using PostgreSQL 18 pgvector:")
        for p in pairs:
            similarity = 1.0 - p["cos_dist"]
            print(f"    * Dist: {p['cos_dist']:.4f} | Sim: {similarity:.4f} | '{p['title_a'][:30]}' <=> '{p['title_b'][:30]}'")

        # Ensure vectors are distinct
        for p in pairs:
            assert p["cos_dist"] > 0.05, f"Vectors unexpectedly identical or near-identical: dist={p['cos_dist']}"

        print("\n================================================================================")
        print("EMBEDDING PROVENANCE REMEDIATION COMPLETED SUCCESSFULLY WITH 100% INTEGRITY!")
        print("================================================================================")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
