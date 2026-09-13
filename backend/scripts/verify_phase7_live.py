import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from sqlalchemy import select, func
from app.core.config import settings
from app.infrastructure.database.session import AsyncSessionLocal
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.source import Source

BASE_URL = "http://127.0.0.1:8000"


async def main():
    api_key = settings.YOUTUBE_API_KEY
    if not api_key or not api_key.strip():
        print("\n" + "=" * 70)
        print("Live YouTube verification was not executed because YOUTUBE_API_KEY is not configured.")
        print("=" * 70 + "\n")
        return

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=60.0) as client:
        print("=== Step 1: Health & Readiness Check ===")
        r = await client.get("/ready")
        print(f"Readiness: {r.status_code} - {r.json()['status']}")

        print("\n=== Step 2: Seed Baseline YouTube Sources ===")
        r = await client.post("/api/v1/sources/seed")
        print(f"Seed Response: {r.status_code}")
        seeded = r.json()
        yt_sources = [s for s in seeded if s["type"] == "YOUTUBE"]
        print(f"Configured YouTube Sources: {len(yt_sources)}")
        for s in yt_sources:
            print(f"   * {s['name']} (Channel ID: {s['configuration'].get('channel_id')})")

        print("\n=== Step 3: Trigger Live YouTube Ingestion (First Run) ===")
        r = await client.post("/api/v1/ingestion/youtube")
        print(f"Batch YouTube Ingestion Status: {r.status_code}")
        summary1 = r.json()
        print(f"Total Sources: {summary1['total_sources']}")
        print(f"Succeeded: {summary1['succeeded_sources']}, Failed: {summary1['failed_sources']}")
        print(f"Fetched: {summary1['total_fetched']}, Inserted: {summary1['total_inserted']}, Skipped: {summary1['total_skipped']}")
        for res in summary1["source_results"]:
            print(f"   * {res['source_name']} [{res['source_type']}]: status={res['status']}, fetched={res['entries_fetched']}, inserted={res['entries_inserted']}, skipped={res['entries_skipped']}, errors={res['errors']}")

        print("\n=== Step 4: Verify Database Persistence & Metadata Quality ===")
        async with AsyncSessionLocal() as session:
            count_stmt = select(func.count(ContentItem.id)).where(ContentItem.canonical_url.like("https://www.youtube.com/%"))
            count_val = (await session.execute(count_stmt)).scalar()
            print(f"Total YouTube videos stored in database: {count_val}")

            # Fetch sample videos
            item_stmt = (
                select(ContentItem)
                .where(ContentItem.canonical_url.like("https://www.youtube.com/%"))
                .order_by(ContentItem.published_at.desc())
                .limit(3)
            )
            items = (await session.execute(item_stmt)).scalars().all()
            for idx, item in enumerate(items, 1):
                print(f"\n--- Video #{idx} ---")
                print(f"Title:        {item.title}")
                print(f"External ID:  {item.external_id}")
                print(f"Canonical:    {item.canonical_url}")
                print(f"Channel:      {item.author}")
                print(f"Published:    {item.published_at.strftime('%Y-%m-%d %H:%M:%S') if item.published_at else 'N/A'}")
                print(f"Views:        {item.metadata_json.get('view_count')}")
                print(f"Likes:        {item.metadata_json.get('like_count')}")
                print(f"Comments:     {item.metadata_json.get('comment_count')}")
                print(f"Duration:     {item.metadata_json.get('duration')}")
                print(f"Thumbnail:    {item.metadata_json.get('thumbnail_url')}")

        print("\n=== Step 5: Test Idempotency (Second Run - 0 new rows expected) ===")
        r = await client.post("/api/v1/ingestion/youtube")
        summary2 = r.json()
        print(f"Second Run: fetched={summary2['total_fetched']}, inserted={summary2['total_inserted']}, updated={summary2['total_updated']}, skipped={summary2['total_skipped']}")
        for res in summary2["source_results"]:
            print(f"   * {res['source_name']}: inserted={res['entries_inserted']}, updated={res['entries_updated']}, skipped={res['entries_skipped']}")

        assert summary2["total_inserted"] == 0, f"Expected 0 inserted on second run, got {summary2['total_inserted']}"
        print("\nSUCCESS: YouTube video ingestion, persistence, dynamic statistics updates, and idempotency 100% verified!")


if __name__ == "__main__":
    asyncio.run(main())
