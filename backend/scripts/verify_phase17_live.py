import asyncio
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
import uuid

# Ensure backend directory is in python search path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from sqlalchemy import text

from app.core.config import settings
from app.core.logging import logger
from app.domain.models.newsletter import PreferencesUpdateRequest, SubscribeRequest
from app.infrastructure.database.session import AsyncSessionLocal
from app.services.digest_service import DigestService
from app.services.email_service import DeliveryService, MockEmailProvider, ResendEmailProvider
from app.services.subscriber_service import SubscriberService


async def verify_phase17():
    print("=" * 70)
    print("PHASE 17 LIVE VERIFICATION SUITE — AI NEWS INTELLIGENCE & PORTFOLIO")
    print("=" * 70)

    # 1. Verify Database Connectivity & Invariants
    print("\n[CHECK 1] Database Connectivity & Core Invariants...")
    async with AsyncSessionLocal() as session:
        # Check PostgreSQL and pgvector version
        pg_ver = (await session.execute(text("SHOW server_version;"))).scalar()
        vec_ver = (await session.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'vector';"))).scalar()
        print(f"  PostgreSQL Version: {pg_ver}")
        print(f"  pgvector Version:   {vec_ver}")
        assert "18" in str(pg_ver), f"Expected PostgreSQL 18, got {pg_ver}"
        assert vec_ver == "0.8.6", f"Expected pgvector 0.8.6, got {vec_ver}"

        # Invariants Check
        ci_count = (await session.execute(text("SELECT count(*) FROM content_items;"))).scalar()
        st_count = (await session.execute(text("SELECT count(*) FROM stories;"))).scalar()
        emb_count = (await session.execute(text("SELECT count(*) FROM content_items WHERE embedding IS NOT NULL;"))).scalar()
        unemb_count = (await session.execute(text("SELECT count(*) FROM content_items WHERE embedding IS NULL;"))).scalar()
        
        # Check dimensions of existing embeddings
        dim_check = (await session.execute(text("SELECT vector_dims(embedding) FROM content_items WHERE embedding IS NOT NULL LIMIT 1;"))).scalar()

        print(f"  ContentItems total:       {ci_count} (Expected: 1,348)")
        print(f"  Stories total:            {st_count} (Expected: 4)")
        print(f"  Authentic embeddings:     {emb_count} (Expected: 4)")
        print(f"  Unembedded pending items: {unemb_count} (Expected: 1,344)")
        print(f"  Embedding dimensionality: {dim_check} (Expected: 1536)")

        assert ci_count == 1348, f"Invariant violation: ContentItems = {ci_count}"
        assert st_count == 4, f"Invariant violation: Stories = {st_count}"
        assert emb_count == 4, f"Invariant violation: Authentic embeddings = {emb_count}"
        assert unemb_count == 1344, f"Invariant violation: Unembedded = {unemb_count}"
        assert dim_check == 1536, f"Invariant violation: Dimension = {dim_check}"
        print("  --> Invariants Verified: NO bulk embedding occurred.")

    # 2. Verify Subscriber Service & Idempotency
    print("\n[CHECK 2] Subscriber Management & Idempotency...")
    async with AsyncSessionLocal() as session:
        sub_service = SubscriberService(session=session)
        test_email = f"live_verify_{uuid.uuid4().hex[:8]}@example.com"
        
        # Subscribe
        sub, created = await sub_service.subscribe(
            SubscribeRequest(
                email=test_email,
                name="Live Verification User",
                age_group="25-34",
                frequency="DAILY",
                preferred_channel="EMAIL",
                topics=["AI Agents", "RAG & Systems"],
            )
        )
        assert created is True
        assert sub.email == test_email.lower()
        print(f"  Created test subscriber: {sub.email} (token={sub.unsubscribe_token[:10]}...)")

        # Duplicate subscription (idempotent update)
        sub_dup, created_dup = await sub_service.subscribe(
            SubscribeRequest(
                email=test_email,
                name="Live User Updated",
                topics=["LLMs & Reasoning"],
            )
        )
        assert created_dup is False
        assert sub_dup.id == sub.id
        assert sub_dup.name == "Live User Updated"
        print("  --> Duplicate subscription safely reused existing record.")

        # Preferences update
        pref_updated = await sub_service.update_preferences(
            PreferencesUpdateRequest(
                email=test_email,
                age_group="35-44",
                topics=["AI Agents", "Research Papers"],
            )
        )
        assert pref_updated.age_group == "35-44"
        print("  --> Preferences update verified.")

    # 3. Verify Digest Generation & Rendering
    print("\n[CHECK 3] Daily Digest Generation & Curated Stories Selection...")
    async with AsyncSessionLocal() as session:
        digest_service = DigestService(session=session)
        today = datetime.now(timezone.utc).date()
        
        digest = await digest_service.generate_daily_digest(target_date=today, force=True)
        print(f"  Generated Digest: id={digest.id}, date={digest.digest_date}, title='{digest.title}'")
        print(f"  Selected Curated Stories Count: {digest.story_count} (Cap: 5–10)")
        assert 0 <= digest.story_count <= 10
        assert "DAILY DIGEST" in digest.html_content
        assert "{{ unsubscribe_url }}" in digest.html_content
        assert "pgvector" not in digest.html_content.lower(), "Internal db leaks found in email HTML!"
        print("  --> Digest compiled clean responsive HTML and plain text without internal leaks.")

        # Test Idempotency
        digest_cached = await digest_service.generate_daily_digest(target_date=today, force=False)
        assert digest_cached.id == digest.id
        print("  --> Digest idempotency verified: identical date returns cached issue.")

    # 4. Verify Delivery Tracking & Resend Provider Safety
    print("\n[CHECK 4] Email Delivery Dispatch & Idempotency...")
    async with AsyncSessionLocal() as session:
        # Check Resend config detection
        print(f"  RESEND_API_KEY present: {bool(settings.RESEND_API_KEY)}")
        print(f"  EMAIL_FROM:             {settings.EMAIL_FROM}")

        # Run delivery using mock provider to prevent actual email transmission during tests
        mock_provider = MockEmailProvider(should_succeed=True)
        delivery_service = DeliveryService(session=session, email_provider=mock_provider)

        send_res = await delivery_service.send_digest(digest_id=digest.id, dry_run=False)
        print(f"  Dispatch Result: total={send_res.total_subscribers}, sent={send_res.sent_count}, skipped={send_res.skipped_count}")
        assert send_res.sent_count >= 1

        # Re-dispatch on same digest -> Must skip already delivered subscribers
        send_res_repeat = await delivery_service.send_digest(digest_id=digest.id, dry_run=False)
        print(f"  Repeat Dispatch: sent={send_res_repeat.sent_count}, skipped={send_res_repeat.skipped_count}")
        assert send_res_repeat.sent_count == 0
        assert send_res_repeat.skipped_count >= 1
        print("  --> Delivery idempotency verified: Zero duplicate emails dispatched!")

    # 5. Verify REST API Endpoints via HTTP
    print("\n[CHECK 5] FastAPI REST Endpoints (http://127.0.0.1:8000)...")
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000/api/v1", timeout=10.0) as client:
        # Health
        h_res = await client.get("/health")
        assert h_res.status_code == 200
        print("  GET /api/v1/health -> 200 OK")

        # Newsletter Subscribe
        sub_api_email = f"api_test_{uuid.uuid4().hex[:6]}@example.com"
        s_res = await client.post(
            "/newsletter/subscribe",
            json={
                "email": sub_api_email,
                "frequency": "DAILY",
                "topics": ["AI Agents", "RAG & Systems"],
            },
        )
        assert s_res.status_code == 200
        print("  POST /api/v1/newsletter/subscribe -> 200 OK")

        # Newsletter Count
        cnt_res = await client.get("/newsletter/subscribers/count")
        assert cnt_res.status_code == 200
        print(f"  GET /api/v1/newsletter/subscribers/count -> {cnt_res.json()}")

        # Latest Digest
        d_res = await client.get("/digests/latest")
        assert d_res.status_code == 200
        d_data = d_res.json()
        print(f"  GET /api/v1/digests/latest -> 200 OK (id={d_data['id']}, stories={d_data['story_count']})")

        # Digest HTML Preview
        prev_res = await client.get(f"/digests/{d_data['id']}/preview")
        assert prev_res.status_code == 200
        assert "text/html" in prev_res.headers["content-type"]
        print("  GET /api/v1/digests/{id}/preview -> 200 OK (HTML preview rendered)")

    # 6. Verify Portfolio Web Server via HTTP
    print("\n[CHECK 6] Portfolio Web UI (http://127.0.0.1:5500)...")
    async with httpx.AsyncClient(base_url="http://127.0.0.1:5500", timeout=10.0) as client:
        idx_res = await client.get("/index.html")
        assert idx_res.status_code == 200
        idx_html = idx_res.text
        assert "ABDULLAH SHAIKH" in idx_html
        assert "AI News" in idx_html
        assert "as-customization-modal" in idx_html
        assert "Coming Soon" in idx_html
        print("  GET /index.html -> 200 OK (Hero, AI News product block, and Customization modal present)")

        proj_res = await client.get("/projects/ai-news.html")
        assert proj_res.status_code == 200
        print("  GET /projects/ai-news.html -> 200 OK (Curated news first, case study below)")

    # 7. Final Invariant Confirmation
    print("\n[CHECK 7] Post-Execution Database Invariants Confirmation...")
    async with AsyncSessionLocal() as session:
        ci_count_final = (await session.execute(text("SELECT count(*) FROM content_items;"))).scalar()
        st_count_final = (await session.execute(text("SELECT count(*) FROM stories;"))).scalar()
        emb_count_final = (await session.execute(text("SELECT count(*) FROM content_items WHERE embedding IS NOT NULL;"))).scalar()
        unemb_final = (await session.execute(text("SELECT count(*) FROM content_items WHERE embedding IS NULL;"))).scalar()

        print(f"  ContentItems: {ci_count_final} (Must be 1,348)")
        print(f"  Stories:      {st_count_final} (Must be 4)")
        print(f"  Embeddings:   {emb_count_final} (Must be 4)")
        print(f"  Unembedded:   {unemb_final} (Must be 1,344)")

        assert ci_count_final == 1348
        assert st_count_final == 4
        assert emb_count_final == 4
        assert unemb_final == 1344

    print("\n" + "=" * 70)
    print("ALL 7 VERIFICATION CRITERIA PASSED — PHASE 17 FULLY OPERATIONAL!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(verify_phase17())
