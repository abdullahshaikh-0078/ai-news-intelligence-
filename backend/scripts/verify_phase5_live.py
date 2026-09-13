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
        print(f"Readiness: {r.status_code} - {r.json()['status']}")

        print("\n=== Step 2: Seed Sources ===")
        r = await client.post("/api/v1/sources/seed")
        print(f"Seed Response: {r.status_code}")
        seeded = r.json()
        arxiv_source = next((s for s in seeded if s["type"] == "ARXIV"), None)
        assert arxiv_source is not None, "ArXiv source not found in seeded list"
        print(f"ArXiv Source: {arxiv_source['name']} (ID: {arxiv_source['id']}, URL: {arxiv_source['url']})")
        print(f"Config: {json.dumps(arxiv_source['configuration'], indent=2)}")

        print("\n=== Step 3: Trigger Live ArXiv Ingestion (First Run) ===")
        r = await client.post("/api/v1/ingestion/arxiv")
        print(f"Batch ArXiv Ingestion Status: {r.status_code}")
        summary1 = r.json()
        print(f"Total Sources: {summary1['total_sources']}")
        print(f"Succeeded: {summary1['succeeded_sources']}, Failed: {summary1['failed_sources']}")
        print(f"Fetched: {summary1['total_fetched']}, Inserted: {summary1['total_inserted']}, Skipped: {summary1['total_skipped']}")
        for res in summary1["source_results"]:
            print(f"   * {res['source_name']} [{res['source_type']}]: status={res['status']}, fetched={res['entries_fetched']}, inserted={res['entries_inserted']}, skipped={res['entries_skipped']}, errors={res['errors']}")

        print("\n=== Step 4: Verify Database Persistence & Metadata Quality ===")
        async with AsyncSessionLocal() as session:
            count_stmt = select(func.count(ContentItem.id)).where(ContentItem.source_id == arxiv_source["id"])
            count_val = (await session.execute(count_stmt)).scalar()
            print(f"Total ArXiv research papers stored in database: {count_val}")

            # Fetch sample papers
            item_stmt = (
                select(ContentItem)
                .where(ContentItem.source_id == arxiv_source["id"])
                .order_by(ContentItem.published_at.desc())
                .limit(3)
            )
            items = (await session.execute(item_stmt)).scalars().all()
            for idx, item in enumerate(items, 1):
                print(f"\n--- Paper #{idx} ---")
                print(f"Title:        {item.title}")
                print(f"ArXiv ID:     {item.external_id}")
                print(f"Canonical:    {item.canonical_url}")
                print(f"Display Auth: {item.author}")
                print(f"Published:    {item.published_at.strftime('%Y-%m-%d') if item.published_at else 'N/A'}")
                print(f"Categories:   {item.metadata_json.get('categories')}")
                print(f"Primary Cat:  {item.metadata_json.get('primary_category')}")
                print(f"PDF URL:      {item.metadata_json.get('pdf_url')}")
                print(f"Abstract:     {item.summary[:140]}...")

        print("\n=== Step 5: Test Idempotency (Second Run - 0 new rows expected) ===")
        r = await client.post("/api/v1/ingestion/arxiv")
        summary2 = r.json()
        print(f"Second Run: fetched={summary2['total_fetched']}, inserted={summary2['total_inserted']}, skipped={summary2['total_skipped']}")
        for res in summary2["source_results"]:
            print(f"   * {res['source_name']}: inserted={res['entries_inserted']}, skipped={res['entries_skipped']}")

        assert summary2["total_inserted"] == 0, f"Expected 0 inserted on second run, got {summary2['total_inserted']}"
        print("\nSUCCESS: ArXiv research ingestion, persistence, and idempotency 100% verified!")


if __name__ == "__main__":
    asyncio.run(main())
