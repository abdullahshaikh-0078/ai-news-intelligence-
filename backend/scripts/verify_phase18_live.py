import asyncio
import base64
from datetime import date, datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import sys
import time
import uuid

# Ensure backend directory is in python search path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from sqlalchemy import text

from app.core.config import settings
from app.core.logging import logger
from app.domain.models.dispatch import DailyDispatchRequest
from app.infrastructure.database.session import AsyncSessionLocal
from app.services.dispatch_service import DailyDispatchService
from app.services.email_service import MockEmailProvider
from app.services.webhook_service import ResendWebhookService, verify_svix_signature


async def verify_phase18():
    print("=" * 75)
    print("PHASE 18 LIVE VERIFICATION SUITE — PRODUCTION DAILY DISPATCH & WEBHOOKS")
    print("=" * 75)

    # 1. Verify Database Invariants
    print("\n[CHECK 1] Production Database Invariants & Alembic Revision...")
    async with AsyncSessionLocal() as session:
        pg_ver = (await session.execute(text("SHOW server_version;"))).scalar()
        vec_ver = (await session.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'vector';"))).scalar()
        alembic_head = (await session.execute(text("SELECT version_num FROM alembic_version;"))).scalar()

        ci_count = (await session.execute(text("SELECT count(*) FROM content_items;"))).scalar()
        st_count = (await session.execute(text("SELECT count(*) FROM stories;"))).scalar()
        emb_count = (await session.execute(text("SELECT count(*) FROM content_items WHERE embedding IS NOT NULL;"))).scalar()
        unemb_count = (await session.execute(text("SELECT count(*) FROM content_items WHERE embedding IS NULL;"))).scalar()
        dim_check = (await session.execute(text("SELECT vector_dims(embedding) FROM content_items WHERE embedding IS NOT NULL LIMIT 1;"))).scalar()

        print(f"  PostgreSQL Version:       {pg_ver}")
        print(f"  pgvector Version:         {vec_ver}")
        print(f"  Alembic Head:             {alembic_head} (Expected: f8a9b0c1d2e3)")
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
        assert alembic_head == "f8a9b0c1d2e3", f"Alembic head violation: {alembic_head}"
        print("  --> Invariants Verified: Invariant preservation 100% compliant.")

    # 2. Verify Svix Webhook Signature Protocol
    print("\n[CHECK 2] Svix Webhook Cryptographic Signature Verification...")
    test_secret = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
    test_msg_id = "msg_live_verify_001"
    test_body = b'{"type":"email.delivered","data":{"email_id":"email_live_test"}}'
    test_ts = str(int(time.time()))

    sec_clean = test_secret[6:]
    missing_padding = len(sec_clean) % 4
    if missing_padding:
        sec_clean += "=" * (4 - missing_padding)
    sec_bytes = base64.b64decode(sec_clean)
    to_sign = f"{test_msg_id}.{test_ts}.{test_body.decode('utf-8')}".encode("utf-8")
    sig = base64.b64encode(hmac.new(sec_bytes, to_sign, hashlib.sha256).digest()).decode("utf-8")
    sig_hdr = f"v1,{sig}"

    valid, msg = verify_svix_signature(test_secret, test_msg_id, test_ts, test_body, sig_hdr)
    print(f"  Valid signature check: {valid} ({msg})")
    assert valid is True

    # Tampered signature check
    tampered_valid, tampered_msg = verify_svix_signature(test_secret, test_msg_id, test_ts, b"tampered", sig_hdr)
    print(f"  Tampered payload check: valid={tampered_valid} ({tampered_msg})")
    assert tampered_valid is False
    print("  --> Svix Cryptographic Signature Verification: PASSED.")

    # 3. Verify Daily Dispatch Workflow & Idempotency
    print("\n[CHECK 3] Automated Daily Dispatch Workflow & Strict Idempotency...")
    async with AsyncSessionLocal() as session:
        mock_provider = MockEmailProvider(should_succeed=True)
        dispatch_service = DailyDispatchService(session=session, email_provider=mock_provider)

        test_date = date(2026, 9, 20)
        req = DailyDispatchRequest(
            target_date=test_date,
            run_ingestion=False,
            dry_run=True,
            item_limit=5,
            max_ai_analyses=0,
            max_embeddings=0,
        )

        res1 = await dispatch_service.run_daily_dispatch(req)
        print(f"  Dispatch Pass 1: status={res1.status}, digest_id={res1.digest_id}, stories={res1.digest_stories_count}")
        assert res1.status in ("success", "skipped")

        # Run again with same parameters -> strict idempotency
        res2 = await dispatch_service.run_daily_dispatch(req)
        print(f"  Dispatch Pass 2 (Idempotency check): status={res2.status}, digest_id={res2.digest_id}")
        assert res2.digest_id == res1.digest_id
        assert res2.sent_count == 0  # No duplicate sends
        print("  --> Dispatch Idempotency: PASSED.")

    # 4. Verify Live API Endpoints via HTTP
    print("\n[CHECK 4] Live REST API Endpoints Verification...")
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=15.0) as client:
        # Check /health
        r_health = await client.get("/health")
        print(f"  GET /health -> HTTP {r_health.status_code}")
        assert r_health.status_code == 200

        # Check /api/v1/dispatch/status
        r_status = await client.get("/api/v1/dispatch/status")
        print(f"  GET /api/v1/dispatch/status -> HTTP {r_status.status_code}")
        assert r_status.status_code == 200
        status_data = r_status.json()
        print(f"      Status: {status_data['status']}, Schedule Hour: {status_data['schedule_hour_utc']} UTC")
        print(f"      Active Daily Subscribers: {status_data['active_daily_subscribers']}")

        # Check /api/v1/dispatch/daily (dry-run)
        r_dispatch = await client.post(
            "/api/v1/dispatch/daily",
            json={"dry_run": True, "run_ingestion": False, "item_limit": 5, "max_ai_analyses": 0, "max_embeddings": 0},
        )
        print(f"  POST /api/v1/dispatch/daily -> HTTP {r_dispatch.status_code}")
        assert r_dispatch.status_code == 200
        dispatch_data = r_dispatch.json()
        print(f"      Outcome: {dispatch_data['status']}, Execution ID: {dispatch_data['execution_id']}")

        # Check /api/v1/newsletter/webhooks/resend
        r_webhook = await client.post(
            "/api/v1/newsletter/webhooks/resend",
            json={"type": "email.delivered", "data": {"email_id": "nonexistent"}},
        )
        print(f"  POST /api/v1/newsletter/webhooks/resend -> HTTP {r_webhook.status_code}")
        assert r_webhook.status_code == 200
        print(f"      Outcome: {r_webhook.json().get('status')}")

    print("\n" + "=" * 75)
    print("PHASE 18 PRODUCTION DISPATCH & WEBHOOKS VERIFIED 100% HEALTHY!")
    print("=" * 75)


if __name__ == "__main__":
    asyncio.run(verify_phase18())
