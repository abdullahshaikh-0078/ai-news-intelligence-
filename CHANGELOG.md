# Changelog

All notable changes to the AI News Intelligence Platform will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.10.0] - 2026-09-10 - Phase 9: AI Processing Layer

### Added
- AI Provider Abstraction (`app.ai`):
  - `BaseAIProvider`: Abstract interface for structured content analysis and embedding generation.
  - `OpenAIProvider`: Async integration with official OpenAI SDK using structured response parsing (`client.beta.chat.completions.parse`) and embeddings (`client.embeddings.create`), bounded retries, exponential backoff, and secret sanitization.
  - `MockAIProvider`: Deterministic offline provider for keyless testing generating synthetic summaries, topics, key points, relevance scores, and unit-normalized 1536-dimensional embeddings.
- Canonical AI Processing Service (`app.services.ai_service`):
  - Unified input text compilation with token and cost bounding across all 5 content families.
  - Idempotent execution (skipping already completed items unless `force=True`).
  - Strict fault isolation preserving raw source items upon provider failure.
  - Concurrency control with `asyncio.Semaphore` and session-safe serialization locks.
- Database Migration & Adaptive Vector Column:
  - Alembic revision `f3aa829c7102_add_ai_processing_fields_and_embedding.py`.
  - Added structured AI fields (`ai_summary`, `ai_key_points`, `ai_topics`, `ai_relevance_score`, `ai_model`, `ai_processed_at`, `embedding_model`).
  - Added 1536-dimensional vector embedding column with dual-environment support (native pgvector in Docker/Linux, and automatic compatible domain fallback on local Windows PG 18).
  - Migrated legacy `RAW` states to `PENDING`.
  - Verified full migration reversibility.
- AI REST API Endpoints (`app.api.routes.ai`):
  - `POST /api/v1/ai/process/{content_id}`: Trigger processing for individual item.
  - `POST /api/v1/ai/process`: Batch process pending items with configurable limits.
  - `GET /api/v1/ai/status`: Inspect system-wide processing counts.
- Automated Test Suite & Regression:
  - Added 15 comprehensive unit and integration tests across providers, services, and APIs.
  - Full suite expanded to 118/118 tests passing (100% green).
  - Verified live database records with `backend/scripts/verify_phase9_live.py`.

## [0.9.0] - 2026-09-09 - Phase 8: Unified Content Model & Canonical Ingestion Contracts

### Added
- Canonical Content Classification: Introduced `ContentType` enum (`ARTICLE`, `RESEARCH_PAPER`, `VIDEO`, `COMMUNITY_POST`) in domain models (`app.domain.models.content_item`).
- Canonical Model Schema Extensions:
  - `ContentItemDomain`: Added `content_type: ContentType` and `thumbnail_url: Optional[str]`.
  - `NormalizedArticle`: Added `content_type: ContentType = ContentType.ARTICLE` and `thumbnail_url: Optional[str] = None`.
  - `ContentItem` (SQLAlchemy ORM): Added `content_type = Column(VARCHAR(50), nullable=False, default="ARTICLE", index=True)` and `thumbnail_url = Column(VARCHAR(2048), nullable=True)`.
- Database Migration & Non-Destructive Backfill:
  - Alembic revision `e2cca71edb01_add_content_type_and_thumbnail_to_content_items.py`.
  - Backfilled 1,348 existing records in dev PostgreSQL (`ai_news_intel`): 1,293 `ARTICLE`, 29 `COMMUNITY_POST`, 26 `RESEARCH_PAPER`.
  - Verified migration upgrade, downgrade, and re-upgrade reversibility.
  - Successfully migrated both `ai_news_intel` and `ai_news_intel_test` databases.
- Adapter Canonical Harmonization:
  - `RSSAdapter`: Explicitly assigns `ContentType.ARTICLE` and extracts image enclosure / media thumbnail links into `thumbnail_url`.
  - `OfficialWebAdapter`: Explicitly assigns `ContentType.ARTICLE` and captures card/lead image URLs into `thumbnail_url`.
  - `ArXivAdapter`: Explicitly assigns `ContentType.RESEARCH_PAPER`.
  - `HackerNewsAdapter`: Explicitly assigns `ContentType.COMMUNITY_POST`.
  - `YouTubeAdapter`: Explicitly assigns `ContentType.VIDEO` and extracts top-resolution thumbnail into `thumbnail_url`.
- Content Repository Extensions:
  - `ContentRepository.list_by_content_type(content_type: str, limit: int = 50)`: Query content items filtered by canonical type.
- Verification & Test Suite:
  - Created `backend/tests/test_unified_content_contract.py` with 4 comprehensive tests validating the enum contract, cross-source canonical invariant conformance, database persistence, and adapter assignments.
  - Full suite expanded to 103/103 tests passing (100% green).
  - Created and executed `backend/scripts/verify_phase8_live.py` verifying real PostgreSQL database records.

## [0.8.0] - 2026-09-09 - Phase 7: YouTube Video Ingestion

### Added
- Official YouTube Data API v3 Integration: Direct REST integration with `https://www.googleapis.com/youtube/v3/` for metadata and statistics ingestion with zero browser automation, Selenium, Playwright, or `yt-dlp`.
- Quota-Conscious Uploads Playlist Strategy: Replaces expensive `search.list` calls (100 units) with a lightweight 2-unit approach:
  - Uploads playlist ID derivation (`UC...` -> `UU...`) or cached/channel lookup (`channels.list`).
  - Recent video retrieval via `playlistItems.list` (1 unit).
  - Batch metadata, duration, and statistics retrieval for all video IDs via `videos.list` (1 unit).
- `YouTubeAdapter` (`app.ingestion.adapters.youtube_adapter`): High-reliability adapter implementing `BaseIngestionAdapter` for `SourceType.YOUTUBE`:
  - Automatic channel ID extraction from configuration or channel URLs.
  - Normalization into `NormalizedArticle` with canonical URL `https://www.youtube.com/watch?v={video_id}`.
  - Stable external identity: `youtube:{video_id}`.
  - Video description parsing into `summary` and `raw_content`.
  - Rich metadata extraction: `view_count`, `like_count`, `comment_count`, `duration`, `tags`, `category_id`, thumbnail resolutions (maxres, standard, high, medium, default).
  - Clean error handling with `YouTubeAdapterError`.
- Orchestration Enhancements (`app.ingestion.orchestrator`):
  - Dynamic Metrics Change Detection: In-place updating of `view_count`, `like_count`, and `comment_count` in `metadata_json` on repeat ingestion runs without creating duplicate database rows.
  - Added `ingest_all_youtube_sources()` with per-source fault isolation.
- Source Registry Baseline Channels: Added 3 authoritative AI YouTube channels:
  - `Two Minute Papers` (`channel_id`: `UCbfYPyITQ-7l4upoX8nvctg`, slug: `two-minute-papers`)
  - `Yannic Kilcher` (`channel_id`: `UCEBm0xLn28l-H0V586dJ7qw`, slug: `yannic-kilcher`)
  - `AI Explained` (`channel_id`: `UCNJ1Ymd5yFuUPtn21xtRbbw`, slug: `ai-explained`)
- API Route: Mounted `POST /api/v1/ingestion/youtube` batch ingestion endpoint alongside single-source endpoint `POST /api/v1/ingestion/sources/{id}`.
- Automated Test Suite: Expanded to 99 tests (100% passing) with unit tests for playlist resolution, thumbnail selection, timestamp parsing, missing statistics, dynamic metrics updates, idempotency, and fault isolation.

## [0.7.0] - 2026-09-09 - Phase 6: Hacker News & Community Signals Ingestion

### Added
- Official Hacker News API Integration: Direct REST integration with the official Firebase Hacker News API (`https://hacker-news.firebaseio.com/v0/`) for `/topstories` and `/beststories`, requiring zero browser automation or HTML scraping.
- `HackerNewsAdapter` (`app.ingestion.adapters.hackernews_adapter`): High-concurrency adapter featuring:
  - Parallel Item Retrieval: Concurrent item fetching controlled by `asyncio.Semaphore(15)` to maximize async throughput while respecting upstream limits.
  - Deterministic AI Keyword Filtering: In-flight evaluation of title and URL against curated AI/ML keywords (`ai`, `llm`, `gpt`, `claude`, `gemini`, `deepseek`, `openai`, `anthropic`, `agent`, etc.) with regex word boundary protection (`\b`) to prevent false positives (e.g. "email", "brainstorm").
  - Self-Post (Ask HN / Show HN) Support: Clean HTML stripping and whitespace normalization, mapping story text into `summary` and `raw_content`.
  - Rich Community Metadata: Capture of community metrics (`score`, `comments`), submitter `author`, `domain`, direct `hn_url`, item timestamps, and self-post boolean flag in `metadata_json`.
  - Configurable minimum score thresholds (default: 5 points).
- Orchestrator Enhancements (`app.ingestion.orchestrator`):
  - Configuration-Based Dispatch: Routing `SourceType.WEB` sources with `config["adapter_type"] == "hacker_news"` to `HackerNewsAdapter`.
  - Cross-Source URL Collision Resolution: Automatic fallback to HN discussion thread URL if an external submission URL is already owned by another registered source in `ContentItem.canonical_url`, preventing DB unique constraint violations while preserving the original link in `metadata_json["article_url"]`.
  - Dynamic Metrics Change Detection: In-place updating of `score` and `comments` in `metadata_json` on subsequent ingestion passes without generating duplicate database records.
  - `ingest_all_hacker_news_sources()`: Batch execution method with per-source exception isolation.
- Source Registry Seeding: Registered baseline `Hacker News AI & Tech` source (`slug="hacker-news"`, `SourceType.WEB`, `adapter_type="hacker_news"`, feeds: `["topstories", "beststories"]`, `min_score=5`).
- API Routing: Added `POST /api/v1/ingestion/hacker-news` endpoint with full OpenAPI documentation and batch summary response.
- Live Real-World Verification: Ingested 29 live AI community stories from the official Firebase API into PostgreSQL 18 with 100% verified idempotency and verified dynamic metric updates on second run (0 inserted, 10 updated, 19 skipped).
- Automated Test Suite: Expanded to 86 tests (100% passing) covering keyword regex filtering, HTML stripping, external stories, self-posts, non-story discarding, batch/single ingestion API, dynamic metric updates, cross-source collisions, and fault isolation.

## [0.6.0] - 2026-09-07 - Phase 5: ArXiv Research Ingestion

### Added
- Official ArXiv API Integration: Full support for official query API (`https://export.arxiv.org/api/query`) in Atom XML format, respecting rate-limit intervals (default 3.0s delay between pages) without browser automation or PDF downloading.
- `ArXivAdapter` (`app.ingestion.adapters.arxiv_adapter`): Ingestion adapter implementing `SourceType.ARXIV` handling:
  - Stable identity resolution: Deterministic regex parsing of modern (`YYMM.NNNNN`) and legacy (`arch-ive/YYMMNNN`) ArXiv IDs into base `external_id` (e.g. `2501.12345`).
  - Stable Canonical URLs: Versionless abstract URLs (`https://arxiv.org/abs/{base_id}`) mapped cleanly into `ContentItem.canonical_url`.
  - Version lifecycle: Preserves paper version tags (e.g. `v2`) and versioned links in `metadata_json`. Subsequent versions update the existing database record rather than producing duplicate rows.
  - Multi-author preservation: Formatted display string in `ContentItem.author` with complete author list preserved in `metadata_json["authors"]`.
  - Multiline whitespace normalization for titles and abstracts.
  - Category and taxonomy preservation: Primary category in `metadata_json["primary_category"]` and all category codes (`cs.AI`, `cs.LG`, `cs.CV`, `cs.CL`, `cs.NE`, `stat.ML`) preserved in `categories` and `metadata_json`.
  - Research metadata: Extraction of DOI, journal references, comments, and direct HTTPS PDF links into `metadata_json`.
  - Configurable pagination: Sequential fetching supporting `start`, `max_results_per_page`, and `max_pages`.
- Source Registry Baseline Enhancement: Updated `arxiv-ai` source definition with comprehensive AI/ML research search query (`cat:cs.AI OR cat:cs.LG OR cat:cs.CL OR cat:cs.CV OR cat:cs.NE OR cat:stat.ML`), pagination, and sorting settings.
- Orchestrator Integration & API:
  - Registered `SourceType.ARXIV` with `ArXivAdapter` in `IngestionOrchestrator`.
  - Added `ingest_all_arxiv_sources()` with per-source error isolation.
  - Added `POST /api/v1/ingestion/arxiv` batch ingestion route.
- Live Real-World Verification: Ingested 25 live research papers from the official ArXiv API into PostgreSQL 18 with 100% verified idempotency (0 duplicates on second run).
- Automated Test Suite: Expanded to 74 tests (100% passing) covering identity parsing, multiline normalization, metadata extraction, version updating, pagination, batch and single-source API routes, and fault isolation.

## [0.5.0] - 2026-09-07 - Phase 4: Official AI Organizations Ingestion

### Added
- Authoritative Organization Ingestion: Full support for official primary AI research & announcement organizations: OpenAI, Anthropic, and Google DeepMind.
- `OfficialWebAdapter` (`app.ingestion.adapters.web_adapter`): Dual-mode adapter checking source structured `feed_url` configuration first, then falling back to clean HTML card parsing.
- `AnthropicNewsParser`: Clean semantic HTML parser extracting news cards, publication dates, category badges (`Research`, `Announcements`, `Product`), and article summaries without browser automation or headless overhead.
- `GenericHTMLCardParser`: Semantic `<article>` and card container fallback parser with relative URL resolution and relative date normalization.
- Source Registry Seeding: Pre-configured first-party sources with high reliability score (`0.98`):
  - `openai-news`: `https://openai.com/news` with feed URL `https://openai.com/news/rss.xml` (1,170+ articles).
  - `anthropic-news`: `https://www.anthropic.com/news` using `AnthropicNewsParser`.
  - `deepmind-blog`: `https://deepmind.google/blog/rss.xml` (100 articles).
- Ingestion Orchestration & API:
  - Added `ingest_all_official_sources()` to `IngestionOrchestrator` targeting authoritative organization sources.
  - Added `POST /api/v1/ingestion/official` route for batch official source ingestion.
  - Fault-tolerant execution isolating source-level network or parsing errors without disrupting other organizations.
- Live Real-World Verification: Ingested 1,283 real articles across OpenAI, Anthropic, and Google DeepMind into PostgreSQL 18 with 100% verified idempotency (0 duplicates on repeat runs).
- Automated Test Suite: Expanded to 61 tests (100% passing) adding HTML card extraction tests, feed URL prioritization tests, official batch/single API tests, and fault isolation tests.

## [0.4.0] - 2026-09-07 - Phase 3: RSS / Atom Ingestion Pipeline

### Added
- Reusable Ingestion Architecture: `BaseIngestionAdapter` abstract interface for multi-source ingestion.
- `RSSAdapter`: Production-ready feed parser supporting RSS 2.0 and Atom feeds, with malformed entry isolation and fallback date extraction.
- `FeedHttpClient`: Async client with exponential backoff retry on 5xx errors, configurable connection/read timeouts, 10MB response-size guard, and User-Agent identification.
- URL Canonicalizer: `canonicalize_url` utility removing tracking parameters (`utm_*`, `fbclid`, `gclid`), fragments, and redundant trailing slashes.
- SHA-256 Content Fingerprinting: `generate_content_hash` utility for article idempotency.
- Database Schema Evolution via Alembic migration `2026_09_07_1200-d1bbec0dcfae_add_summary_external_id_language_to_.py`:
  - Added `summary (TEXT)`, `external_id (VARCHAR(512))`, `language (VARCHAR(10))` columns to `content_items`.
  - Added compound index `ix_content_items_source_external` on `(source_id, external_id)`.
- `ContentRepository`: Added `get_by_external_id()`, `count_by_source()`, and `update()` methods.
- `IngestionOrchestrator`: Dispatches to registered adapters, coordinates idempotent persistence (skip if hash matches, update if changed, insert if new), updates source `last_fetched_at`, and isolates errors per source.
- Ingestion REST API: `/api/v1/ingestion/rss` (batch ingestion) and `/api/v1/ingestion/sources/{source_id}` (single-source trigger).
- Real Feed Verification: Successfully ingested MIT Technology Review AI RSS feed into live PostgreSQL 18 instance with verified idempotency (0 duplicates on repeat runs).
- Expanded automated test suite from 35 to 53 tests (100% passing) covering parsing, HTTP retries, canonicalization, idempotency, and API routes.

## [0.3.0] - 2026-09-07 - Phase 2: Source Registry System

### Added
- Source domain model and `SourceType` controlled enum supporting `RSS`, `WEB`, `ARXIV`, `YOUTUBE`, and `GITHUB`.
- Pydantic v2 schemas: `SourceCreate`, `SourceUpdate`, `SourceResponse`, and paginated `SourceListResponse`.
- Schema evolution via Alembic migration `2026_09_07_1129-dfe6a65ca915_add_language_and_constraints_to_sources.py` adding `language` column and database-level `CheckConstraint` bounds on `reliability_score` (0.0 to 1.0) and `fetch_interval_minutes` (>= 5).
- Expanded `SourceRepository` with URL lookup, multi-criteria filtered listing (type, enabled), and keyset/offset pagination with exact count queries.
- Created `SourceService` with strict duplicate URL prevention (`EntityConflictError`), slug auto-generation/collision disambiguation, lifecycle mutation (enable/disable), and idempotent baseline source seeding.
- Mounted versioned REST API router at `/api/v1/sources` with full CRUD, enable/disable endpoints, filtering, pagination, and `/seed` endpoint.
- Baseline verified AI sources registered across all 5 source types: ArXiv AI Research, MIT Technology Review AI, OpenAI News, Two Minute Papers, and GitHub Trending Python.
- Comprehensive test suite expansion with 23 new tests (35 total tests passing) spanning schemas, repository, service rules, and API endpoints.

## [0.2.0] - 2026-09-07 - Phase 1: Core Backend & Data Access Layer

### Added
- Canonical PostgreSQL database configuration with connection pooling and unified `DATABASE_URL`.
- Dedicated local PostgreSQL 18 cluster with `ai_news_intel` and `ai_news_intel_test` databases.
- Async SQLAlchemy 2.0 database engine, `async_sessionmaker`, and `get_db` FastAPI dependency with automatic commit/rollback lifecycle.
- Declarative Base and `TimestampMixin` for consistent timestamp tracking.
- Initial foundational domain models and SQLAlchemy ORM models:
  - `Source`: Upstream feed and content registry with configuration JSON and reliability scoring.
  - `Story`: Clustered synthesized narrative entity with importance score and curation flags.
  - `ContentItem`: Raw ingested article evidence preserving full provenance back to source and story.
- Decoupled repository layer with `BaseRepository`, `SourceRepository`, `ContentRepository`, and `StoryRepository`.
- Connected Alembic migrations to `Base.metadata`, generated `2026_09_07_0741-9d54944eede9_create_initial_models.py`, and verified clean upgrade/downgrade behavior.
- Operational probes distinguishing process liveness (`GET /health`, `GET /api/v1/health`) and database readiness (`GET /ready`, `GET /api/v1/ready`) with graceful 503 degraded status when database is unavailable.
- Structured application error handling with custom `AppException` hierarchy and sanitization of database errors.
- Comprehensive automated test suite with 12 tests covering database lifecycle, repositories, migrations, liveness, and readiness probes.
- Updated Next.js frontend to monitor both liveness and database readiness in real-time.

## [0.1.0] - 2026-09-07 - Phase 0: Greenfield Project Initialization

### Added
- Independent greenfield project architecture and directory structure.
- Root configuration and documentation: `.env.example`, `.gitignore`, `README.md`, `ARCHITECTURE.md`, `DEVELOPMENT.md`, `CHANGELOG.md`, `docker-compose.yml`.
- FastAPI backend baseline skeleton with structured logging, Pydantic settings configuration, and clean layer separation (`api`, `core`, `domain`, `services`, `ingestion`, `ai`, `infrastructure`).
- Health endpoint (`GET /health`) with status, environment, version, and timestamp metadata.
- Backend automated test suite using `pytest` and `httpx.AsyncClient`.
- Next.js 15 frontend shell with modern dark-mode command center styling, live backend health connection monitor, and responsive discovery UI.
- Alembic database migration scaffolding.
