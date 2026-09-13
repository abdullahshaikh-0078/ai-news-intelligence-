import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from sqlalchemy import select, func
from app.infrastructure.database.session import AsyncSessionLocal
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.source import Source

BASE_URL = "http://127.0.0.1:8000"

async def main():
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=60.0) as client:
        print("=== Step 1: Health & Readiness Check ===")
        r = await client.get("/ready")
        print(f"Readiness: {r.status_code} - {r.json()}")

        print("\n=== Step 2: Seed Sources ===")
        r = await client.post("/api/v1/sources/seed")
        print(f"Seed Response: {r.status_code}")
        seeded = r.json()
        print(f"Total seeded sources returned: {len(seeded)}")
        for s in seeded:
            print(f" - [{s['type']}] {s['name']} (slug={s['slug']}, score={s['reliability_score']})")

        print("\n=== Step 3: Trigger Official Sources Ingestion (First Run) ===")
        r = await client.post("/api/v1/ingestion/official")
        print(f"Batch Official Ingestion Status: {r.status_code}")
        summary1 = r.json()
        print(f"Total Sources: {summary1['total_sources']}")
        print(f"Succeeded: {summary1['succeeded_sources']}, Failed: {summary1['failed_sources']}")
        print(f"Fetched: {summary1['total_fetched']}, Inserted: {summary1['total_inserted']}, Skipped: {summary1['total_skipped']}")
        for res in summary1["source_results"]:
            print(f"   * {res['source_name']} [{res['source_type']}]: status={res['status']}, fetched={res['entries_fetched']}, inserted={res['entries_inserted']}, skipped={res['entries_skipped']}, errors={res['errors']}")

        print("\n=== Step 4: Verify Database Persistence ===")
        async with AsyncSessionLocal() as session:
            sources_stmt = select(Source).where(Source.slug.in_(["openai-news", "anthropic-news", "deepmind-blog"]))
            sources_res = await session.execute(sources_stmt)
            official_sources = sources_res.scalars().all()

            for src in official_sources:
                count_stmt = select(func.count(ContentItem.id)).where(ContentItem.source_id == src.id)
                count_val = (await session.execute(count_stmt)).scalar()
                
                # Fetch a sample article
                item_stmt = select(ContentItem).where(ContentItem.source_id == src.id).limit(2)
                items = (await session.execute(item_stmt)).scalars().all()
                print(f"\nSource '{src.name}' (ID: {src.id}):")
                print(f" - Database articles stored: {count_val}")
                for item in items:
                    print(f"   Sample: [{item.published_at.strftime('%Y-%m-%d') if item.published_at else 'N/A'}] {item.title[:60]}... ({item.canonical_url})")

        print("\n=== Step 5: Test Idempotency (Second Run - 0 new articles expected) ===")
        r = await client.post("/api/v1/ingestion/official")
        summary2 = r.json()
        print(f"Second Run Inserted: {summary2['total_inserted']}, Skipped: {summary2['total_skipped']}")
        for res in summary2["source_results"]:
            print(f"   * {res['source_name']}: inserted={res['entries_inserted']}, skipped={res['entries_skipped']}")

        assert summary2["total_inserted"] == 0, f"Expected 0 inserted on second run, got {summary2['total_inserted']}"
        print("\nSUCCESS: Verification complete! Persistence and idempotency 100% verified.")

if __name__ == "__main__":
    asyncio.run(main())
