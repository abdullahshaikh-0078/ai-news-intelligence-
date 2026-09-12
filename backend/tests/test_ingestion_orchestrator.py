import pytest
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from app.infrastructure.models.source import Source
from app.infrastructure.repositories.source_repo import SourceRepository
from app.ingestion.http_client import FeedFetchError
from app.ingestion.orchestrator import IngestionOrchestrator

GOOD_FEED = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
<channel>
    <title>Good Feed</title>
    <link>https://good.org</link>
    <item>
        <title>Good Story</title>
        <link>https://good.org/story-1</link>
        <description>Good story summary</description>
    </item>
</channel>
</rss>
"""


@pytest.mark.asyncio
async def test_orchestrator_fault_isolation_and_filters(db_session: AsyncSession, monkeypatch):
    """Verify that disabled sources are ignored and a failure in one feed does not stop remaining feeds."""
    source_repo = SourceRepository(db_session)
    tag = uuid.uuid4().hex[:6]

    # Source 1: Enabled RSS (Will fail HTTP)
    s_failing = Source(
        name=f"Failing Feed {tag}",
        slug=f"failing-{tag}",
        type="RSS",
        url=f"https://failing-{tag}.org/feed.xml",
        enabled=True,
    )
    # Source 2: Enabled RSS (Will succeed)
    s_success = Source(
        name=f"Success Feed {tag}",
        slug=f"success-{tag}",
        type="RSS",
        url=f"https://success-{tag}.org/feed.xml",
        enabled=True,
    )
    # Source 3: Disabled RSS (Should not be fetched)
    s_disabled = Source(
        name=f"Disabled Feed {tag}",
        slug=f"disabled-{tag}",
        type="RSS",
        url=f"https://disabled-{tag}.org/feed.xml",
        enabled=False,
    )
    # Source 4: Non-RSS source (e.g. YOUTUBE, should not be included in RSS batch)
    s_youtube = Source(
        name=f"YouTube Feed {tag}",
        slug=f"youtube-{tag}",
        type="YOUTUBE",
        url=f"https://youtube-{tag}.org/feed.xml",
        enabled=True,
    )

    await source_repo.add(s_failing)
    await source_repo.add(s_success)
    await source_repo.add(s_disabled)
    await source_repo.add(s_youtube)

    async def mock_fetch(self, url):
        if "failing" in url:
            raise FeedFetchError("Connection timed out to failing host")
        return GOOD_FEED

    monkeypatch.setattr("app.ingestion.http_client.FeedHttpClient.fetch", mock_fetch)

    orchestrator = IngestionOrchestrator(db_session)
    summary = await orchestrator.ingest_all_rss_sources()

    # Total RSS enabled sources should be at least 2
    assert summary.failed_sources >= 1
    assert summary.succeeded_sources >= 1
    assert summary.total_inserted >= 1

    # Verify that the successful source got its last_fetched_at timestamp updated
    refreshed_success = await source_repo.get_by_id(s_success.id)
    assert refreshed_success.last_fetched_at is not None

    # Verify disabled source was never touched
    refreshed_disabled = await source_repo.get_by_id(s_disabled.id)
    assert refreshed_disabled.last_fetched_at is None
