# PRODUCTION OPERATIONS & RELIABILITY GUIDE
**AI News Intelligence & Synthesis Platform**

---

## 1. Production Architecture Overview

The AI News Intelligence Platform is designed for high reliability, minimal operating cost, and zero data loss. It transforms raw multi-source streams into high-signal, clustered, and ranked news intelligence with automated newsletter compilation and delivery tracking.

```
       INGESTION (RSS, ArXiv, Official Blogs, Hacker News, YouTube)
                              ↓
                NORMALIZATION & CANONICALIZATION
                              ↓
                 DETERMINISTIC DEDUPLICATION
                              ↓
              AI ANALYSIS (gemini-3.6-flash, Bounded)
                              ↓
          ON-DEMAND EMBEDDING (gemini-embedding-001, 1536-dim)
                              ↓
                 SEMANTIC DEDUPLICATION (pgvector <=>)
                              ↓
            STORY CLUSTERING & MULTI-SOURCE SYNTHESIS
                              ↓
                 TRANSPARENT 6-SIGNAL RANKING
                              ↓
                   EDITORIAL STORY CURATION
                              ↓
                 DETERMINISTIC DAILY DIGEST
                              ↓
              SCHEDULED NEWSLETTER DISPATCH (Resend)
                              ↓
               DELIVERY AUDIT & WEBHOOK INGESTION
```

---

## 2. Critical Production Invariants & Policies

### Strict On-Demand Embedding Policy:
- **Baseline:** 1,348 total ContentItems, with exactly 4 authentic Gemini 1536-dimensional embeddings and 1,344 historical items in PENDING state.
- **Rule:** **NEVER bulk-embed the historical 1,344 items.**
- **Rationale:** Pre-embedding the entire historical database would waste extensive API quotas on stale or low-relevance content. Only newly ingested content that passes candidate relevance thresholds receives on-demand embeddings.

### Zero Mock Vectors in Production:
- Synthetic, fake, or mock vectors are strictly forbidden in production tables.
- All active vector rows must use authentic `gemini-embedding-001` vectors with native 1536 float dimensionality.

### Idempotent Scheduling & Deliveries:
- Running the daily dispatch twice on the same calendar day returns the existing digest issue without recompilation.
- Delivery records enforce unique constraint `(digest_id, subscriber_id)` preventing duplicate emails to subscribers who have already received an issue.

---

## 3. Quota & Rate-Limit Management

### Google Gemini API Protection:
- **Models:** `gemini-3.6-flash` (Analysis) and `gemini-embedding-001` (Embeddings).
- **Circuit Breaker:** If Gemini returns `429 RESOURCE_EXHAUSTED`, the pipeline logs a warning, halts external AI calls gracefully, and falls back to existing embeddings and pre-computed analyses.
- **Retry Logic:** Exponential backoff with 3 retries for transient 500/503 errors.

### Resend Email Limits:
- Free Tier permits up to 100 emails per day.
- Dispatch batches emails to active subscribers with `frequency == 'DAILY'`. Inactive or unsubscribed users are automatically excluded.

---

## 4. Security & Cryptographic Protocols

### Webhook Signature Verification (Svix Protocol):
- Every inbound webhook to `/api/v1/newsletter/webhooks/resend` is cryptographically validated using HMAC-SHA256 against `RESEND_WEBHOOK_SECRET`.
- **Replay Attack Mitigation:** Inbound timestamps exceeding 300 seconds of clock skew are rejected immediately with HTTP 400.
- **Tampering Resistance:** Signatures are compared using constant-time string comparison (`hmac.compare_digest`).

### Unsubscribe Tokens:
- Unsubscribe links include an unguessable 32-byte cryptographic token (`secrets.token_urlsafe(32)`).
- One-click unsubscribe does not require password entry or subscriber login.

### Database Query Sanitation:
- All database interactions use SQLAlchemy 2.0 async mapped queries with parameterized binding.
- Exception handlers intercept internal database errors and sanitize responses to generic `DATABASE_ERROR` envelopes with HTTP 503, preventing schema exposure.

---

## 5. Backup, Maintenance & Rollback Procedures

### Daily Database Backups (pg_dump):
```bash
# Automated backup command
pg_dump -U postgres -h localhost -p 5433 -Fc ai_news_intel > ai_news_backup_$(date +%Y%m%d).dump
```

### Schema Rollback (Alembic):
To roll back a migration cleanly:
```bash
alembic downgrade -1
```

### Emergency Restart:
If the database daemon stops unexpectedly:
```powershell
& "C:\Program Files\PostgreSQL\18\bin\postgres.exe" -D "backend/data/postgres" -p 5433
```
FastAPI ASGI restart:
```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8000
```
