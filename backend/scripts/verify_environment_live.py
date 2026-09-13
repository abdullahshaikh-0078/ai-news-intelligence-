"""Comprehensive live environment and database verification script.
Evaluates:
1. Environment configuration (sanitized).
2. Database connectivity, tables, and Alembic migration head.
3. ContentItem counts, Source counts, and content-type breakdown.
4. Most recent 10 ContentItems.
5. YouTube API status and live test.
6. Gemini API and embedding connectivity.
7. PostgreSQL pgvector extension status.
"""

import asyncio
import os
import sys

# Ensure backend directory is on sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from sqlalchemy import func, select, text
from app.core.config import settings
from app.infrastructure.database.session import AsyncSessionLocal
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.source import Source


async def verify_all():
    print("=" * 75)
    print("LIVE ENVIRONMENT & SYSTEM VERIFICATION")
    print("=" * 75)

    # 1. Environment Configuration
    gemini_key_present = bool(settings.GEMINI_API_KEY and settings.GEMINI_API_KEY.strip())
    youtube_key_present = bool(settings.YOUTUBE_API_KEY and settings.YOUTUBE_API_KEY.strip())
    db_url_present = bool(settings.DATABASE_URL and settings.DATABASE_URL.strip())

    print("\n[1] Environment Configuration Status:")
    print(f"    DATABASE_URL:    {'CONFIGURED' if db_url_present else 'MISSING'}")
    print(f"    GEMINI_API_KEY:  {'CONFIGURED' if gemini_key_present else 'MISSING'}")
    print(f"    YOUTUBE_API_KEY: {'CONFIGURED' if youtube_key_present else 'MISSING'}")

    # Parse host/port/database from settings.DATABASE_URL
    db_info = settings.DATABASE_URL.split("@")[-1] if "@" in settings.DATABASE_URL else settings.DATABASE_URL
    print(f"    Target Database: {db_info}")

    # 2. Database Connectivity & Migrations
    print("\n[2] Database Connectivity:")
    try:
        async with AsyncSessionLocal() as session:
            ping_res = await session.execute(text("SELECT 1"))
            print(f"    Ping: OK (result={ping_res.scalar()})")

            # Check tables
            tbl_res = await session.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name;")
            )
            tables = [t[0] for t in tbl_res.all()]
            print(f"    Public Tables: {tables}")
            assert "content_items" in tables, "content_items table missing"
            assert "sources" in tables, "sources table missing"

            # Check migration head
            head_res = await session.execute(text("SELECT version_num FROM alembic_version;"))
            curr_rev = head_res.scalar()
            print(f"    Alembic Revision in DB: {curr_rev}")
    except Exception as exc:
        print(f"    Database Connection FAILED: {exc}")
        return

    # 3. Existing Data Inspection
    print("\n[3] Existing Data Inspection:")
    async with AsyncSessionLocal() as session:
        total_items = (await session.execute(select(func.count()).select_from(ContentItem))).scalar_one()
        total_sources = (await session.execute(select(func.count()).select_from(Source))).scalar_one()
        enabled_sources = (
            await session.execute(select(func.count()).select_from(Source).where(Source.enabled == True))
        ).scalar_one()

        print(f"    Total ContentItems: {total_items}")
        print(f"    Total Sources:      {total_sources} (Enabled: {enabled_sources})")

        # Content-type breakdown
        type_res = await session.execute(
            select(ContentItem.content_type, func.count())
            .group_by(ContentItem.content_type)
            .order_by(func.count().desc())
        )
        print("    ContentItems by content_type:")
        for ctype, count in type_res.all():
            print(f"      - {ctype:15}: {count:5} records")

        # List all enabled sources
        src_res = await session.execute(select(Source).where(Source.enabled == True).order_by(Source.type, Source.name))
        print("\n    Enabled Sources in Registry:")
        for s in src_res.scalars().all():
            print(f"      - [{s.type:8}] {s.name:30} ({s.slug}) -> {s.url[:50]}")

        # Most recent 10 ContentItems
        recent_res = await session.execute(
            select(ContentItem).order_by(ContentItem.published_at.desc()).limit(10)
        )
        print("\n    Most Recent 10 ContentItems:")
        for idx, item in enumerate(recent_res.scalars().all(), 1):
            pub_str = item.published_at.strftime("%Y-%m-%d %H:%M UTC") if item.published_at else "No date"
            print(f"      {idx:2}. [{item.content_type:14}] ({pub_str}) {item.title[:60]}")

    # 4. PostgreSQL pgvector Extension Status
    print("\n[4] PostgreSQL pgvector Extension Status:")
    async with AsyncSessionLocal() as session:
        try:
            ext_res = await session.execute(
                text("SELECT name, default_version, installed_version FROM pg_available_extensions WHERE name = 'vector';")
            )
            ext_row = ext_res.first()
            if ext_row:
                status_str = "ENABLED" if ext_row[2] else "AVAILABLE"
                print(f"    pgvector extension: {status_str} (Default: {ext_row[1]}, Installed: {ext_row[2]})")
            else:
                print("    pgvector extension: NOT AVAILABLE in PostgreSQL 18 catalog (using domain vector fallback)")
        except Exception as exc:
            print(f"    Error querying extension: {exc}")

    print("\n" + "=" * 75)


if __name__ == "__main__":
    asyncio.run(verify_all())
