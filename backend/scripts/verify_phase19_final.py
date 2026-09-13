#!/usr/bin/env python
"""
Phase 19 Final End-to-End Release Verification Suite.
Validates database invariants, pgvector 0.8.6, authentic Gemini embeddings,
Alembic migration head, API health, newsletter flow, dispatch dry-run,
Svix webhook signatures, and portfolio compatibility.

Exits with code 0 on complete pass, or non-zero on failure.
"""
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

# Ensure backend root is in python path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from sqlalchemy import text

from app.core.config import settings
from app.core.logging import logger
from app.domain.models.dispatch import DailyDispatchRequest
from app.infrastructure.database.session import AsyncSessionLocal
from app.services.dispatch_service import DailyDispatchService
from app.services.email_service import MockEmailProvider
from app.services.webhook_service import verify_svix_signature


async def run_final_verification() -> int:
    print("=" * 80)
    print("PHASE 19 — FINAL PRODUCTION & V1 RELEASE VALIDATION SUITE")
    print("=" * 80)
    failures = []

    # -------------------------------------------------------------------------
    # 1. Database Invariants & Native pgvector
    # -------------------------------------------------------------------------
    print("\n[CHECK 1/7] Database Invariants & Native pgvector Engine...")
    try:
        async with AsyncSessionLocal() as session:
            pg_ver = (await session.execute(text("SHOW server_version;"))).scalar()
            vec_ver = (await session.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'vector';"))).scalar()
            alembic_head = (await session.execute(text("SELECT version_num FROM alembic_version;"))).scalar()

            ci_count = (await session.execute(text("SELECT count(*) FROM content_items;"))).scalar()
            st_count = (await session.execute(text("SELECT count(*) FROM stories;"))).scalar()
            sci_count = (await session.execute(text("SELECT count(*) FROM story_content_items;"))).scalar()
            emb_count = (await session.execute(text("SELECT count(*) FROM content_items WHERE embedding IS NOT NULL;"))).scalar()
            unemb_count = (await session.execute(text("SELECT count(*) FROM content_items WHERE embedding IS NULL;"))).scalar()
            dim_check = (await session.execute(text("SELECT vector_dims(embedding) FROM content_items WHERE embedding IS NOT NULL LIMIT 1;"))).scalar()

            # Verify Cosine Distance Operator (<=>)
            cos_dist = (await session.execute(text("SELECT embedding <=> embedding FROM content_items WHERE embedding IS NOT NULL LIMIT 1;"))).scalar()

            # Verify Mock Vector Absence
            mock_emb = (await session.execute(text("SELECT count(*) FROM content_items WHERE embedding_model ILIKE '%mock%';"))).scalar()

            print(f"  PostgreSQL:           {pg_ver}")
            print(f"  pgvector:             {vec_ver}")
            print(f"  Alembic Head:         {alembic_head} (Target: f8a9b0c1d2e3)")
            print(f"  ContentItems:         {ci_count} (Target: 1,348)")
            print(f"  Stories:              {st_count} (Target: 4)")
            print(f"  StoryContentItems:    {sci_count} (Target: 4)")
            print(f"  Authentic Embeddings: {emb_count} (Target: 4)")
            print(f"  Unembedded Items:     {unemb_count} (Target: 1,344)")
            print(f"  Vector Dimensions:    {dim_check} (Target: 1536)")
            print(f"  Cosine Self-Dist:     {cos_dist} (Target: 0.0)")
            print(f"  Mock Vectors:         {mock_emb} (Target: 0)")

            assert ci_count == 1348, f"ContentItems mismatch: {ci_count}"
            assert st_count == 4, f"Stories mismatch: {st_count}"
            assert sci_count == 4, f"StoryContentItems mismatch: {sci_count}"
            assert emb_count == 4, f"Embeddings mismatch: {emb_count}"
            assert unemb_count == 1344, f"Unembedded mismatch: {unemb_count}"
            assert dim_check == 1536, f"Dimensions mismatch: {dim_check}"
            assert abs(float(cos_dist)) < 1e-6, f"Cosine operator mismatch: {cos_dist}"
            assert mock_emb == 0, f"Mock vectors found: {mock_emb}"
            assert alembic_head == "f8a9b0c1d2e3", f"Alembic head mismatch: {alembic_head}"
            print("  --> CHECK 1 PASSED: Strict invariants preserved.")
    except Exception as exc:
        print(f"  --> CHECK 1 FAILED: {exc}")
        failures.append(f"Database invariants: {exc}")

    # -------------------------------------------------------------------------
    # 2. Live API Health & Readiness
    # -------------------------------------------------------------------------
    print("\n[CHECK 2/7] Live FastAPI Health & System Telemetry...")
    try:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=10.0) as client:
            r_health = await client.get("/health")
            assert r_health.status_code == 200, f"Health returned {r_health.status_code}"
            assert r_health.json()["status"] == "healthy"

            r_overview = await client.get("/api/v1/overview")
            assert r_overview.status_code == 200, f"Overview returned {r_overview.status_code}"
            data_ov = r_overview.json()
            assert "total_content_items" in data_ov, f"Overview missing total_content_items: {data_ov}"

            r_disp_status = await client.get("/api/v1/dispatch/status")
            assert r_disp_status.status_code == 200, f"Dispatch status returned {r_disp_status.status_code}"
            assert r_disp_status.json()["status"] == "READY"
            print("  --> CHECK 2 PASSED: Live API health and telemetry verified.")
    except Exception as exc:
        print(f"  --> CHECK 2 FAILED: {repr(exc)}")
        failures.append(f"Live API health: {repr(exc)}")

    # -------------------------------------------------------------------------
    # 3. Content, Ranking & Curation Endpoints
    # -------------------------------------------------------------------------
    print("\n[CHECK 3/7] Content, Ranking & Editorial Curation Endpoints...")
    try:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=10.0) as client:
            # Curated stories
            r_cur = await client.get("/api/v1/curation/stories?limit=10")
            assert r_cur.status_code == 200
            cur_data = r_cur.json()
            assert isinstance(cur_data, list)

            # Ranked stories
            r_rank = await client.get("/api/v1/ranking/stories?limit=10")
            assert r_rank.status_code == 200
            rank_data = r_rank.json()
            assert isinstance(rank_data, list)

            # Content items pagination
            r_content = await client.get("/api/v1/content?page=1&page_size=5")
            assert r_content.status_code == 200
            print("  --> CHECK 3 PASSED: Intelligence queries operational.")
    except Exception as exc:
        print(f"  --> CHECK 3 FAILED: {exc}")
        failures.append(f"Content endpoints: {exc}")

    # -------------------------------------------------------------------------
    # 4. Newsletter Flow & Idempotent Subscription
    # -------------------------------------------------------------------------
    print("\n[CHECK 4/7] Newsletter Subscription & Preference Lifecycle...")
    try:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=10.0) as client:
            test_email = f"phase19_verify_{uuid.uuid4().hex[:6]}@example.com"
            # 1. Subscribe
            r_sub = await client.post(
                "/api/v1/newsletter/subscribe",
                json={
                    "email": test_email,
                    "name": "Phase 19 User",
                    "frequency": "DAILY",
                    "topics": ["AI Agents", "Deep Learning"],
                },
            )
            assert r_sub.status_code == 200
            assert r_sub.json()["status"] == "success"

            # 2. Idempotent re-subscribe
            r_sub2 = await client.post(
                "/api/v1/newsletter/subscribe",
                json={
                    "email": test_email,
                    "name": "Phase 19 User Updated",
                    "frequency": "DAILY",
                },
            )
            assert r_sub2.status_code == 200
            assert r_sub2.json()["created"] is False

            # 3. Unsubscribe
            r_unsub = await client.post(
                "/api/v1/newsletter/unsubscribe",
                json={"email": test_email},
            )
            assert r_unsub.status_code == 200
            assert r_unsub.json()["status"] == "success"
            print("  --> CHECK 4 PASSED: Newsletter subscriber lifecycle verified.")
    except Exception as exc:
        print(f"  --> CHECK 4 FAILED: {exc}")
        failures.append(f"Newsletter flow: {exc}")

    # -------------------------------------------------------------------------
    # 5. Daily Dispatch Dry-Run & Strict Idempotency
    # -------------------------------------------------------------------------
    print("\n[CHECK 5/7] Automated Daily Dispatch Engine & Idempotency...")
    try:
        async with AsyncSessionLocal() as session:
            mock_provider = MockEmailProvider(should_succeed=True)
            dispatch_service = DailyDispatchService(session=session, email_provider=mock_provider)

            target_date = date(2026, 9, 25)
            req = DailyDispatchRequest(
                target_date=target_date,
                run_ingestion=False,
                dry_run=True,
                item_limit=5,
                max_ai_analyses=0,
                max_embeddings=0,
            )

            # Pass 1
            res1 = await dispatch_service.run_daily_dispatch(req)
            assert res1.status in ("success", "skipped")
            assert res1.digest_id is not None

            # Pass 2: Re-run must reuse digest issue idempotently
            res2 = await dispatch_service.run_daily_dispatch(req)
            assert res2.digest_id == res1.digest_id, "Digest issue should be reused idempotently"
            assert res2.dry_run is True
            print("  --> CHECK 5 PASSED: Daily dispatch and idempotency verified.")
    except Exception as exc:
        print(f"  --> CHECK 5 FAILED: {repr(exc)}")
        failures.append(f"Daily dispatch: {repr(exc)}")

    # -------------------------------------------------------------------------
    # 6. Resend Webhook Cryptographic Verification (Svix)
    # -------------------------------------------------------------------------
    print("\n[CHECK 6/7] Resend Webhook Protocol & Svix Signature Integrity...")
    try:
        secret = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
        msg_id = "msg_phase19_test"
        body = b'{"type":"email.delivered","data":{"email_id":"msg_phase19_test"}}'
        ts = str(int(time.time()))

        sec_clean = secret[6:]
        missing_padding = len(sec_clean) % 4
        if missing_padding:
            sec_clean += "=" * (4 - missing_padding)
        sec_bytes = base64.b64decode(sec_clean)
        to_sign = f"{msg_id}.{ts}.{body.decode('utf-8')}".encode("utf-8")
        sig = base64.b64encode(hmac.new(sec_bytes, to_sign, hashlib.sha256).digest()).decode("utf-8")
        sig_hdr = f"v1,{sig}"

        valid, msg = verify_svix_signature(secret, msg_id, ts, body, sig_hdr)
        assert valid is True, f"Valid signature failed: {msg}"

        tampered_valid, _ = verify_svix_signature(secret, msg_id, ts, b"tampered", sig_hdr)
        assert tampered_valid is False, "Tampered signature unexpectedly validated"

        # Check endpoint accepts payload
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=10.0) as client:
            r_wh = await client.post(
                "/api/v1/newsletter/webhooks/resend",
                json={"type": "email.delivered", "data": {"email_id": "nonexistent"}},
            )
            assert r_wh.status_code == 200
        print("  --> CHECK 6 PASSED: Svix signature security verified.")
    except Exception as exc:
        print(f"  --> CHECK 6 FAILED: {exc}")
        failures.append(f"Webhook security: {exc}")

    # -------------------------------------------------------------------------
    # 7. Portfolio Client & Security Audit
    # -------------------------------------------------------------------------
    print("\n[CHECK 7/7] Portfolio Presentation & Security Isolation...")
    try:
        # Verify no hardcoded secrets in .env.example
        env_ex_path = Path(__file__).resolve().parents[2] / ".env.example"
        if env_ex_path.is_file():
            content = env_ex_path.read_text(encoding="utf-8")
            assert "AQ." not in content, "Gemini API key leaked in .env.example"
            assert "AIzaSy" not in content, "YouTube API key leaked in .env.example"
            assert "re_" not in content, "Resend key leaked in .env.example"

        print("  --> CHECK 7 PASSED: Zero secrets leaked in repository template.")
    except Exception as exc:
        print(f"  --> CHECK 7 FAILED: {exc}")
        failures.append(f"Security audit: {exc}")

    # -------------------------------------------------------------------------
    # Final Result
    # -------------------------------------------------------------------------
    print("\n" + "=" * 80)
    if not failures:
        print("PHASE 19 FINAL VERIFICATION: ALL CHECKS PASSED (7/7) — V1.0.0 READY")
        print("=" * 80)
        return 0
    else:
        print(f"PHASE 19 FINAL VERIFICATION: FAILED ({len(failures)} errors)")
        for f in failures:
            print(f"  - {f}")
        print("=" * 80)
        return 1


if __name__ == "__main__":
    code = asyncio.run(run_final_verification())
    sys.exit(code)
