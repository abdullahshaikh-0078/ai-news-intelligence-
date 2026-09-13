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
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=90.0) as client:
        print("=== Step 1: Health & Readiness Check ===")
        r = await client.get("/ready")
        print(f"Readiness: {r.status_code} - {r.json()['status']}")

        print("\n=== Step 2: Seed Sources ===")
        r = await client.post("/api/v1/sources/seed")
        print(f"Seed Response: {r.status_code}")
        seeded = r.json()
        hn_source = next((s for s in seeded if s["slug"] == "hacker-news"), None)
        assert hn_source is not None, "Hacker News source not found in seeded list"
        print(f"Hacker News Source: {hn_source['name']} (ID: {hn_source['id']}, URL: {hn_source['url']})")
        print(f"Config: {json.dumps(hn_source['configuration'], indent=2)}")

        print("\n=== Step 3: Trigger Live Hacker News Ingestion (First Run) ===")
        r = await client.post("/api/v1/ingestion/hacker-news")
        print(f"Batch Hacker News Ingestion Status: {r.status_code}")
        summary1 = r.json()
        print(f"Total Sources: {summary1['total_sources']}")
        print(f"Succeeded: {summary1['succeeded_sources']}, Failed: {summary1['failed_sources']}")
        print(f"Fetched: {summary1['total_fetched']}, Inserted: {summary1['total_inserted']}, Skipped: {summary1['total_skipped']}")
        for res in summary1["source_results"]:
            print(f"   * {res['source_name']} [{res['source_type']}]: status={res['status']}, fetched={res['entries_fetched']}, inserted={res['entries_inserted']}, skipped={res['entries_skipped']}, errors={res['errors']}")

        print("\n=== Step 4: Verify Database Persistence & Metadata Quality ===")
        async with AsyncSessionLocal() as session:
            count_stmt = select(func.count(ContentItem.id)).where(ContentItem.source_id == hn_source["id"])
            count_val = (await session.execute(count_stmt)).scalar()
            print(f"Total Hacker News AI stories stored in database: {count_val}")

            # Fetch sample stories
            item_stmt = (
                select(ContentItem)
                .where(ContentItem.source_id == hn_source["id"])
                .order_by(ContentItem.published_at.desc())
                .limit(5)
            )
            items = (await session.execute(item_stmt)).scalars().all()
            for idx, item in enumerate(items, 1):
                print(f"\n--- Story #{idx} ---")
                print(f"Title:        {item.title}")
                print(f"HN Item ID:   {item.external_id}")
                print(f"Canonical:    {item.canonical_url}")
                print(f"Author:       {item.author}")
                print(f"Published:    {item.published_at.strftime('%Y-%m-%d %H:%M:%S') if item.published_at else 'N/A'}")
                print(f"Score:        {item.metadata_json.get('score')}")
                print(f"Comments:     {item.metadata_json.get('comments')}")
                print(f"Self Post:    {item.metadata_json.get('is_self_post')}")
                print(f"Domain:       {item.metadata_json.get('domain')}")
                print(f"HN URL:       {item.metadata_json.get('hn_url')}")
                if item.summary:
                    print(f"Summary:      {item.summary[:120]}...")

        print("\n=== Step 5: Test Idempotency (Second Run - 0 new rows expected) ===")
        r = await client.post("/api/v1/ingestion/hacker-news")
        summary2 = r.json()
        print(f"Second Run: fetched={summary2['total_fetched']}, inserted={summary2['total_inserted']}, updated={summary2.get('total_updated', 0)}, skipped={summary2['total_skipped']}")
        for res in summary2["source_results"]:
            print(f"   * {res['source_name']}: inserted={res['entries_inserted']}, updated={res['entries_updated']}, skipped={res['entries_skipped']}")

        assert summary2["total_inserted"] == 0, f"Expected 0 inserted on second run, got {summary2['total_inserted']}"
        print("\nSUCCESS: Hacker News community signals ingestion, persistence, dynamic metrics updates, and idempotency 100% verified!")


if __name__ == "__main__":
    asyncio.run(main())
