import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.repositories.content_repo import ContentRepository
from app.infrastructure.repositories.source_repo import SourceRepository
from app.ingestion.http_client import FeedFetchError
from app.ingestion.orchestrator import IngestionOrchestrator
from app.services.source_service import SourceService

HN_TOPSTORIES_MOCK = [41000001, 41000002, 41000003]

ITEM_41000001 = {
    "id": 41000001,
    "type": "story",
    "by": "sama",
    "time": 1725840000,
    "title": "Frontier AI Safety Evaluations Release",
    "url": "https://openai.com/index/safety-evals?utm_source=hn",
    "score": 100,
    "descendants": 40,
}

ITEM_41000001_UPDATED = {
    "id": 41000001,
    "type": "story",
    "by": "sama",
    "time": 1725840000,
    "title": "Frontier AI Safety Evaluations Release",
    "url": "https://openai.com/index/safety-evals?utm_source=hn",
    "score": 145,
    "descendants": 72,
}

ITEM_41000002 = {
    "id": 41000002,
    "type": "story",
    "by": "anthropic_dev",
    "time": 1725841000,
    "title": "Ask HN: Building Autonomous Agents with Claude",
    "text": "Discussion on multi-turn tool calling and reasoning architectures.",
    "score": 85,
    "descendants": 50,
}

ITEM_41000003 = {
    "id": 41000003,
    "type": "story",
    "by": "random_user",
    "time": 1725842000,
    "title": "Why I enjoy woodworking on weekends",
    "score": 25,
    "descendants": 10,
}


@pytest.mark.asyncio
async def test_hn_source_seeding(db_session: AsyncSession):
    """Verify that Hacker News baseline source is seeded with correct configuration."""
    source_repo = SourceRepository(db_session)
    service = SourceService(source_repo)

    seeded = await service.seed_default_sources()
    slug_map = {s.slug: s for s in seeded}

    assert "hacker-news" in slug_map
    hn_src = slug_map["hacker-news"]
    assert hn_src.type == "WEB"
    assert hn_src.config.get("adapter_type") == "hacker_news"
    assert hn_src.reliability_score == 0.88


@pytest.mark.asyncio
async def test_hn_ingestion_and_idempotency(db_session: AsyncSession, monkeypatch):
    """Verify end-to-end Hacker News story ingestion and zero-write idempotency."""
    source_repo = SourceRepository(db_session)
    content_repo = ContentRepository(db_session)
    service = SourceService(source_repo)
    seeded = await service.seed_default_sources()
    hn_src = next(s for s in seeded if s.slug == "hacker-news")

    async def mock_fetch_json(self, url):
        if "topstories.json" in url or "beststories.json" in url:
            return HN_TOPSTORIES_MOCK
        elif "item/41000001.json" in url:
            return ITEM_41000001
        elif "item/41000002.json" in url:
            return ITEM_41000002
        elif "item/41000003.json" in url:
            return ITEM_41000003
        return None

    monkeypatch.setattr("app.ingestion.http_client.FeedHttpClient.fetch_json", mock_fetch_json)

    orchestrator = IngestionOrchestrator(db_session)

    # 1. First Ingestion Run: 41000001 (AI external) and 41000002 (AI self-post) accepted; 41000003 rejected (non-AI)
    res1 = await orchestrator.ingest_source(hn_src)
    assert res1.status == "SUCCESS"
    assert res1.entries_fetched == 2
    assert res1.entries_inserted == 2
    assert res1.entries_skipped == 0

    # Verify persisted items
    item1 = await content_repo.get_by_external_id(hn_src.id, "hn:41000001")
    assert item1 is not None
    assert item1.title == "Frontier AI Safety Evaluations Release"
    assert item1.canonical_url == "https://openai.com/index/safety-evals"
    assert item1.metadata_json["score"] == 100
    assert item1.metadata_json["comments"] == 40

    item2 = await content_repo.get_by_external_id(hn_src.id, "hn:41000002")
    assert item2 is not None
    assert item2.canonical_url == "https://news.ycombinator.com/item?id=41000002"
    assert item2.metadata_json["score"] == 85

    # 2. Second Ingestion Run -> Strict Idempotency Check
    res2 = await orchestrator.ingest_source(hn_src)
    assert res2.status == "SUCCESS"
    assert res2.entries_fetched == 2
    assert res2.entries_inserted == 0
    assert res2.entries_skipped == 2


@pytest.mark.asyncio
async def test_hn_dynamic_metrics_update(db_session: AsyncSession, monkeypatch):
    """Verify that score and comment count updates modify existing record without duplicate rows."""
    source_repo = SourceRepository(db_session)
    content_repo = ContentRepository(db_session)
    service = SourceService(source_repo)
    seeded = await service.seed_default_sources()
    hn_src = next(s for s in seeded if s.slug == "hacker-news")

    # Step 1: Ingest initial score (100)
    async def mock_fetch_initial(self, url):
        if "topstories.json" in url or "beststories.json" in url:
            return [41000001]
        elif "item/41000001.json" in url:
            return ITEM_41000001
        return None

    monkeypatch.setattr("app.ingestion.http_client.FeedHttpClient.fetch_json", mock_fetch_initial)
    orchestrator = IngestionOrchestrator(db_session)
    res1 = await orchestrator.ingest_source(hn_src)
    assert res1.entries_inserted == 1

    # Step 2: Score increases to 145, comments to 72
    async def mock_fetch_updated(self, url):
        if "topstories.json" in url or "beststories.json" in url:
            return [41000001]
        elif "item/41000001.json" in url:
            return ITEM_41000001_UPDATED
        return None

    monkeypatch.setattr("app.ingestion.http_client.FeedHttpClient.fetch_json", mock_fetch_updated)
    res2 = await orchestrator.ingest_source(hn_src)
    assert res2.entries_inserted == 0
    assert res2.entries_updated == 1
    assert res2.entries_skipped == 0

    # Verify updated record in place
    updated_item = await content_repo.get_by_external_id(hn_src.id, "hn:41000001")
    assert updated_item is not None
    assert updated_item.metadata_json["score"] == 145
    assert updated_item.metadata_json["comments"] == 72


@pytest.mark.asyncio
async def test_hn_cross_source_url_collision_handling(db_session: AsyncSession, monkeypatch):
    """Verify that an HN submission linking to an already-ingested RSS article does not fail or overwrite."""
    source_repo = SourceRepository(db_session)
    content_repo = ContentRepository(db_session)
    service = SourceService(source_repo)
    seeded = await service.seed_default_sources()
    rss_src = next(s for s in seeded if s.slug == "mit-tech-review-ai")
    hn_src = next(s for s in seeded if s.slug == "hacker-news")

    # 1. Pre-insert an article from RSS source with canonical URL 'https://example.com/shared-story'
    shared_url = "https://example.com/shared-story"
    from datetime import datetime, timezone
    rss_item = ContentItem(
        source_id=rss_src.id,
        canonical_url=shared_url,
        title="RSS Shared AI Breakthrough",
        published_at=datetime.now(timezone.utc),
        fetched_at=datetime.now(timezone.utc),
        content_hash="hash-rss",
        metadata_json={"source": "rss"},
    )
    await content_repo.add(rss_item)

    # 2. Hacker News submits the same external article URL
    hn_shared_item = {
        "id": 41999999,
        "type": "story",
        "by": "hn_user",
        "time": 1725840000,
        "title": "Discussion: Shared AI Breakthrough",
        "url": shared_url,
        "score": 250,
        "descendants": 95,
    }

    async def mock_fetch_json(self, url):
        if "topstories.json" in url:
            return [41999999]
        elif "item/41999999.json" in url:
            return hn_shared_item
        return None

    monkeypatch.setattr("app.ingestion.http_client.FeedHttpClient.fetch_json", mock_fetch_json)

    # 3. Ingest HN source
    orchestrator = IngestionOrchestrator(db_session)
    res = await orchestrator.ingest_source(hn_src)
    assert res.status == "SUCCESS"
    assert res.entries_inserted == 1

    # Verify both items exist with distinct identities
    # RSS item retains original source_id and URL
    rss_db_item = await content_repo.get_by_canonical_url(shared_url)
    assert rss_db_item.source_id == rss_src.id
    assert rss_db_item.title == "RSS Shared AI Breakthrough"

    # HN item fell back to its discussion URL to prevent collision
    hn_db_item = await content_repo.get_by_external_id(hn_src.id, "hn:41999999")
    assert hn_db_item is not None
    assert hn_db_item.source_id == hn_src.id
    assert hn_db_item.canonical_url == "https://news.ycombinator.com/item?id=41999999"
    assert hn_db_item.metadata_json["article_url"] == shared_url


@pytest.mark.asyncio
async def test_hn_fault_isolation(db_session: AsyncSession, monkeypatch):
    """Verify that network error fetching a feed does not halt orchestrator."""
    source_repo = SourceRepository(db_session)
    service = SourceService(source_repo)
    await service.seed_default_sources()

    async def mock_fetch_json(self, url):
        raise FeedFetchError("Firebase gateway timeout 504")

    monkeypatch.setattr("app.ingestion.http_client.FeedHttpClient.fetch_json", mock_fetch_json)

    orchestrator = IngestionOrchestrator(db_session)
    summary = await orchestrator.ingest_all_hacker_news_sources()

    assert summary.total_sources >= 1
    # Even if feed fetch failed gracefully, it returns 0 inserted without unhandled crashes
    assert summary.total_inserted == 0
