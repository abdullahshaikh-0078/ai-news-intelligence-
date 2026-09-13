# System Architecture & Design Specification
## AI News Intelligence & Synthesis Platform (V1.0.0 Release)

---

## 1. Executive Summary & Architectural Principles

The **AI News Intelligence & Synthesis Platform** is a production-grade autonomous intelligence system engineered to ingest, normalize, analyze, deduplicate, cluster, rank, curate, and distribute high-signal artificial intelligence developments. It operates on strict software engineering and reliability principles:

1. **Independent Core Domain**: Business rules, scoring algorithms, story clustering, and content models are decoupled from external frameworks, database drivers, and third-party APIs.
2. **Provider Agnosticism**: AI/LLM components interface via abstract provider abstractions (`BaseAIProvider`), allowing zero-downtime provider transitions (Google Gemini, OpenAI, or deterministic local offline mocks).
3. **Ingestion Isolation & Fault Tolerance**: Ingestion adapters operate under bounded concurrency and per-source error containment. Network timeouts or markup changes at any single source never cascade or halt the pipeline.
4. **Deterministic & Multi-Stage Deduplication**: Incoming content passes through deterministic URL canonicalization and SHA-256 content fingerprinting, followed by on-demand dense vector similarity clustering.
5. **Cost-Conscious On-Demand Intelligence**: Vector embeddings and LLM analysis are strictly bounded and executed on demand. Historical archives are not bulk-embedded; operations scale with active daily news volume.
6. **Native Relational Vector Store**: Vector persistence relies on native PostgreSQL with the `pgvector` extension (`vector(1536)`), eliminating operational overhead and sync latency of secondary external vector databases.
7. **Strict Idempotency Across All Pipelines**: Ingestion, story clustering, daily digest compilation, email dispatches, and webhook receipts are strictly idempotent.

---

## 2. End-to-End System Architecture

```text
[ UPSTREAM SOURCES ]
  ├── RSS / Atom Feeds (Tech publications, Lab blogs)
  ├── Official AI Lab Portals (OpenAI, Anthropic, DeepMind)
  ├── ArXiv Preprints (cs.AI, cs.LG, cs.CL, cs.CV, stat.ML)
  ├── Developer Discourse (Hacker News Firebase API)
  └── YouTube Video Channels (Google Data API v3)
             │
             ▼
[ INGESTION & NORMALIZATION ]
  ├── URL Canonicalization (tracking strip, version strip)
  ├── Content Fingerprinting (SHA-256)
  └── Unified Content Model (ARTICLE, RESEARCH_PAPER, VIDEO, COMMUNITY_POST)
             │
             ▼
[ STORAGE & DETERMINISTIC DEDUPLICATION ]
  └── PostgreSQL 18.4 + pgvector 0.8.6 (`content_items`)
             │
             ▼
[ ON-DEMAND AI PIPELINE ]
  ├── LLM Analysis (Gemini Chat Model: Summary, Key Points, Relevance)
  └── Vector Generation (gemini-embedding-001: 1536-dimensional native vector)
             │
             ▼
[ SEMANTIC DEDUPLICATION & STORY CLUSTERING ]
  ├── pgvector Cosine Distance (<=>) Cross-Matching
  ├── Temporal Windowing (72h sliding window)
  └── Story Cluster Assignment (`stories`, `story_content_items`)
             │
             ▼
[ MULTI-FACTOR RANKING & CURATION ]
  ├── Explainable Scoring (Authority + Freshness + Velocity + Diversity)
  └── Editorial Curation Engine (Top 5–10 stories constraint)
             │
             ▼
[ DISPATCH & NEWSLETTER SUBSCRIPTION ]
  ├── Daily Automation Runner (`DailyDispatchService`)
  ├── Jinja2 Digest Compilation (HTML + Plaintext)
  ├── Email Dispatch via Resend API
  └── Asynchronous Delivery Webhook Processing (Signature Verified)
             │
             ▼
[ PRESENTATION & PORTFOLIO INTEGRATION ]
  ├── FastAPI REST Endpoints (`/api/v1/...`)
  └── Portfolio Command Center (Modern Web UI + Customization Modal)
```

---

## 3. Detailed Subsystem Specifications

### 3.1 Unified Content Model & Source Registry (Phases 1–8)
- **Entities**:
  - `Source`: Ingestion source metadata (`slug`, `type`, `url`, `reliability_score`, `fetch_interval_minutes`, `config`).
  - `ContentItem`: Raw ingested evidence preserving full provenance (`source_id`, `story_id`, `canonical_url`, `content_hash`, `content_type`, `thumbnail_url`, `raw_content`, `ai_summary`, `ai_key_points`, `ai_topics`, `ai_relevance`, `embedding`, `metadata_json`).
- **Canonical Content Types**: `ARTICLE`, `RESEARCH_PAPER`, `VIDEO`, `COMMUNITY_POST`.
- **Fault-Isolated Adapters**: `RSSAdapter`, `OfficialWebAdapter`, `ArXivAdapter`, `HackerNewsAdapter`, `YouTubeAdapter`.

### 3.2 AI Intelligence & Vector Abstraction (Phases 9, 10, 16)
- **Primary Production Provider**: Google Gemini (`GeminiProvider`).
- **Embedding Specifications**: `gemini-embedding-001`, producing native 1536-dimensional float vectors stored in `vector(1536)`.
- **Chat Synthesis Model**: `gemini-1.5-flash` / `gemini-2.5-flash` with structured Pydantic schema validation.
- **On-Demand Generation Strategy**: Embeddings are calculated only when items are selected for semantic clustering or ranking. Historical archives remain unembedded (`NULL`) to avoid unnecessary cloud API costs and rate limits.

### 3.3 Semantic Deduplication & Story Clustering (Phases 10, 11)
- **Vector Operator**: Native PostgreSQL `pgvector` cosine distance operator (`<=>`).
- **Clustering Mechanics**:
  - Compares candidate item embeddings against active story cluster centroids within a rolling temporal window (e.g., 72 hours).
  - If cosine similarity >= threshold (default 0.82), the item is appended to the existing story cluster.
  - If below threshold, a new `Story` entity is synthesized.
- **Story Entities**:
  - `Story`: Unified narrative entity (`headline`, `summary`, `key_takeaway`, `why_it_matters`, `importance_score`, `category`, `status`).
  - `StoryContentItem`: Association table linking supporting evidence and articles to stories.

### 3.4 Multi-Factor Ranking & Editorial Curation (Phases 12, 13)
- **Ranking Formula**: Transparent, explainable scoring:
  $$\text{Score} = w_a \cdot \text{Authority} + w_f \cdot \text{Freshness} + w_v \cdot \text{Velocity} + w_c \cdot \text{Completeness}$$
- **Editorial Curation Rules**:
  - Daily digest candidate selection is strictly constrained between 5 and 10 stories.
  - Category diversity caps prevent any single domain (e.g., LLM announcements) from dominating the digest.
  - Fallback mechanisms guarantee digest compilation even during sparse news cycles.

### 3.5 Newsletter, Digest & Delivery Engine (Phases 17, 18)
- **Subscriber Management**:
  - `Subscriber`: Email, name, age group, delivery schedule (`DAILY_MORNING`, `DAILY_EVENING`, `WEEKLY`), topic preferences, secure unsubscribe token.
  - Idempotent subscription handling (re-subscribing reactivates unsubscribed accounts).
- **Digest Generator**: Compiles curated stories into responsive, beautifully styled HTML and plain-text fallback templates with one-click secure unsubscribe links.
- **Delivery Execution**:
  - Integration with Resend REST API.
  - Strict delivery idempotency: `DigestDeliveryRecord` prevents sending duplicate digests for the same date/schedule.
- **Webhook Ingestion**:
  - Route: `POST /api/v1/newsletter/webhooks/resend`.
  - Cryptographic signature validation using Svix / HMAC.
  - Replay protection and event status synchronization (`delivered`, `bounced`, `complained`, `opened`, `clicked`).

### 3.6 Frontend & Portfolio Integration (Phases 15, 17)
- **Architecture**: Decoupled product-first interface embedded within the professional engineering portfolio (`abdullah-ai-systems-portfolio`).
- **Client Implementation**: `portfolio-ai-news.js` providing asynchronous data fetching, graceful offline fallback, and modal state management.
- **Subscriber Customization Modal**:
  - Collects subscriber name, age bracket, delivery schedule, and topic tags.
  - Channel selection displays Email (active), with WhatsApp and Telegram explicitly labelled and disabled as "Coming Soon".

---

## 4. Production Deployment & Runtime Architecture

```text
[ Client Traffic (Browsers) ]
              │
              ▼
[ Portfolio / CDN Layer (Vercel) ]
  ├── Static HTML / CSS / JS
  └── Environment: PORTFOLIO_API_BASE_URL -> FastAPI Backend
              │
              ▼ HTTPS (CORS Controlled)
[ Backend API Server (FastAPI on Render / Railway / Fly.io) ]
  ├── Uvicorn ASGI Server (Python 3.11+)
  ├── Operational Probes (/health, /ready)
  └── Bounded Concurrency
              │
              ├──► [ External AI: Google Gemini API (gemini-embedding-001) ]
              ├──► [ External Email: Resend API (Transactional Email) ]
              │
              ▼ Connection Pooling (SQLAlchemy 2.0 Async)
[ Relational + Vector Storage (Neon / Supabase / Managed PostgreSQL) ]
  ├── PostgreSQL 18.4 / 16+
  └── pgvector 0.8.6 (vector(1536), cosine operator <=>)
```

---

## 5. Security & Reliability Architecture

- **Zero Hardcoded Secrets**: All API keys, connection strings, and webhook secrets are injected via system environment variables managed by Pydantic `Settings`.
- **Restricted CORS Policy**: Configurable origins (`CORS_ORIGINS`) allow only trusted deployment domains (e.g. Vercel portfolio URL), rejecting untrusted cross-origin requests.
- **SQL Injection Safety**: Strict SQLAlchemy 2.0 parameterized queries and ORM mappings; zero raw string SQL interpolation.
- **Webhook Replay Protection**: Timestamp tolerance checks and cryptographically signed headers prevent replay attacks on webhook endpoints.
- **Graceful Error Containment**: All external integrations (Gemini, Resend, upstream RSS/APIs) execute inside try/except circuit breakers that log sanitized error codes without exposing tracebacks to end users.
