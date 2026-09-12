import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from app.infrastructure.repositories.content_repo import ContentRepository
from app.infrastructure.repositories.source_repo import SourceRepository
from app.ingestion.http_client import FeedFetchError
from app.ingestion.orchestrator import IngestionOrchestrator
from app.services.source_service import SourceService

OPENAI_FEED_MOCK = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
<channel>
    <title>OpenAI News</title>
    <link>https://openai.com</link>
    <item>
        <title>Frontier System Card Release</title>
        <link>https://openai.com/index/system-card?utm_source=twitter</link>
        <description>Safety evaluations for latest models.</description>
        <pubDate>Mon, 07 Sep 2026 10:00:00 GMT</pubDate>
    </item>
</channel>
</rss>
"""

DEEPMIND_FEED_MOCK = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
<channel>
    <title>Google DeepMind News</title>
    <link>https://deepmind.google</link>
    <item>
        <title>WeatherNext 3 Breakthrough</title>
        <link>https://deepmind.google/blog/weathernext-3/</link>
        <description>Advanced global weather AI prediction system.</description>
        <pubDate>Thu, 03 Sep 2026 15:00:00 GMT</pubDate>
    </item>
</channel>
</rss>
"""

ANTHROPIC_HTML_MOCK = """<!DOCTYPE html>
<html>
<body>
    <a href="/news/alignment-security">
        <span class="badge">Research</span>
        <span class="date">Aug 31, 2026</span>
        <h3>Improving Alignment and Security</h3>
        <p>New empirical methods for evaluating model safeguards.</p>
    </a>
</body>
</html>
"""


@pytest.mark.asyncio
async def test_official_sources_seeding(db_session: AsyncSession):
    """Verify that OpenAI, Anthropic, and Google DeepMind are seeded with 0.98 reliability."""
    source_repo = SourceRepository(db_session)
    service = SourceService(source_repo)

    seeded = await service.seed_default_sources()
    slug_map = {s.slug: s for s in seeded}

    assert "openai-news" in slug_map
    assert "anthropic-news" in slug_map
    assert "deepmind-blog" in slug_map

    openai_src = slug_map["openai-news"]
    assert openai_src.reliability_score == 0.98
    assert openai_src.config.get("organization") == "OpenAI"

    anthropic_src = slug_map["anthropic-news"]
    assert anthropic_src.reliability_score == 0.98
    assert anthropic_src.config.get("organization") == "Anthropic"

    deepmind_src = slug_map["deepmind-blog"]
    assert deepmind_src.reliability_score == 0.98
    assert deepmind_src.config.get("organization") == "Google DeepMind"


@pytest.mark.asyncio
async def test_official_sources_ingestion_and_idempotency(db_session: AsyncSession, monkeypatch):
    """Verify end-to-end official source ingestion and subsequent zero-write idempotency."""
    source_repo = SourceRepository(db_session)
    content_repo = ContentRepository(db_session)
    service = SourceService(source_repo)
    await service.seed_default_sources()

    async def mock_fetch(self, url):
        if "openai.com" in url:
            return OPENAI_FEED_MOCK
        elif "deepmind.google" in url:
            return DEEPMIND_FEED_MOCK
        elif "anthropic.com" in url:
            return ANTHROPIC_HTML_MOCK
        return "<xml></xml>"

    monkeypatch.setattr("app.ingestion.http_client.FeedHttpClient.fetch", mock_fetch)

    orchestrator = IngestionOrchestrator(db_session)

    # 1. First Ingestion Run across all official sources
    summary1 = await orchestrator.ingest_all_official_sources()
    assert summary1.total_sources >= 3
    assert summary1.failed_sources == 0
    assert summary1.total_inserted >= 3
    assert summary1.total_skipped == 0

    # Verify article records in database
    openai_item = await content_repo.get_by_canonical_url("https://openai.com/index/system-card")
    assert openai_item is not None
    assert openai_item.title == "Frontier System Card Release"

    anthropic_item = await content_repo.get_by_canonical_url("https://www.anthropic.com/news/alignment-security")
    assert anthropic_item is not None
    assert anthropic_item.title == "Improving Alignment and Security"
    assert anthropic_item.author == "Anthropic"

    deepmind_item = await content_repo.get_by_canonical_url("https://deepmind.google/blog/weathernext-3")
    assert deepmind_item is not None
    assert deepmind_item.title == "WeatherNext 3 Breakthrough"

    # 2. Second Ingestion Run -> Idempotency Check
    summary2 = await orchestrator.ingest_all_official_sources()
    assert summary2.failed_sources == 0
    assert summary2.total_inserted == 0  # 0 new rows written!
    assert summary2.total_skipped >= 3


@pytest.mark.asyncio
async def test_official_sources_fault_isolation(db_session: AsyncSession, monkeypatch):
    """Verify that failure in Anthropic HTTP fetch does not halt OpenAI or DeepMind."""
    source_repo = SourceRepository(db_session)
    service = SourceService(source_repo)
    await service.seed_default_sources()

    async def mock_fetch(self, url):
        if "anthropic.com" in url:
            raise FeedFetchError("Anthropic endpoint down with 503")
        elif "openai.com" in url:
            return OPENAI_FEED_MOCK
        elif "deepmind.google" in url:
            return DEEPMIND_FEED_MOCK
        return "<xml></xml>"

    monkeypatch.setattr("app.ingestion.http_client.FeedHttpClient.fetch", mock_fetch)

    orchestrator = IngestionOrchestrator(db_session)
    summary = await orchestrator.ingest_all_official_sources()

    # Succeeded should include OpenAI and DeepMind, failed should include Anthropic
    assert summary.succeeded_sources >= 2
    assert summary.failed_sources >= 1
    assert summary.total_inserted >= 2
