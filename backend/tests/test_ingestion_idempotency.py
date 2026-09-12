import pytest
import uuid
from sqlalchemy.ext.asyncio import AsyncSession
from app.infrastructure.models.source import Source
from app.infrastructure.repositories.content_repo import ContentRepository
from app.infrastructure.repositories.source_repo import SourceRepository
from app.ingestion.adapters.rss_adapter import RSSAdapter
from app.ingestion.orchestrator import IngestionOrchestrator

FEED_V1 = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
<channel>
    <title>Idempotency Lab</title>
    <link>https://idempotent.org</link>
    <item>
        <title>Article One</title>
        <link>https://idempotent.org/news/one?utm_medium=rss</link>
        <description>Original summary one</description>
        <guid>guid-item-1</guid>
    </item>
    <item>
        <title>Article Two</title>
        <link>https://idempotent.org/news/two</link>
        <description>Original summary two</description>
        <guid>guid-item-2</guid>
    </item>
</channel>
</rss>
"""

# Same feed, but Article One has an updated summary
FEED_V2 = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
<channel>
    <title>Idempotency Lab</title>
    <link>https://idempotent.org</link>
    <item>
        <title>Article One</title>
        <link>https://idempotent.org/news/one</link>
        <description>Updated summary one with new details</description>
        <guid>guid-item-1</guid>
    </item>
    <item>
        <title>Article Two</title>
        <link>https://idempotent.org/news/two</link>
        <description>Original summary two</description>
        <guid>guid-item-2</guid>
    </item>
</channel>
</rss>
"""


@pytest.mark.asyncio
async def test_feed_ingestion_idempotency(db_session: AsyncSession, monkeypatch):
    """Verify that repeated ingestion of identical feeds never produces duplicate database rows."""
    source_repo = SourceRepository(db_session)
    content_repo = ContentRepository(db_session)

    slug = f"idemp-{uuid.uuid4().hex[:6]}"
    source = Source(
        name="Idempotency Test Source",
        slug=slug,
        type="RSS",
        url=f"https://idempotent.org/{slug}.xml",
        enabled=True,
    )
    await source_repo.add(source)

    current_xml = FEED_V1

    async def mock_fetch(self, url):
        return current_xml

    monkeypatch.setattr("app.ingestion.http_client.FeedHttpClient.fetch", mock_fetch)

    orchestrator = IngestionOrchestrator(db_session)

    # 1. First Ingestion Run -> 2 items inserted
    res1 = await orchestrator.ingest_source(source)
    assert res1.status == "SUCCESS"
    assert res1.entries_fetched == 2
    assert res1.entries_inserted == 2
    assert res1.entries_skipped == 0
    assert res1.entries_updated == 0

    count_after_first = await content_repo.count_by_source(source.id)
    assert count_after_first == 2

    # 2. Second Ingestion Run (Identical feed) -> 0 inserted, 2 skipped
    res2 = await orchestrator.ingest_source(source)
    assert res2.status == "SUCCESS"
    assert res2.entries_fetched == 2
    assert res2.entries_inserted == 0
    assert res2.entries_skipped == 2
    assert res2.entries_updated == 0

    count_after_second = await content_repo.count_by_source(source.id)
    assert count_after_second == 2  # No duplicates created!

    # 3. Third Ingestion Run (Article One updated) -> 0 inserted, 1 updated, 1 skipped
    current_xml = FEED_V2
    res3 = await orchestrator.ingest_source(source)
    assert res3.status == "SUCCESS"
    assert res3.entries_fetched == 2
    assert res3.entries_inserted == 0
    assert res3.entries_updated == 1
    assert res3.entries_skipped == 1

    count_after_third = await content_repo.count_by_source(source.id)
    assert count_after_third == 2

    # Verify updated content in database
    item_one = await content_repo.get_by_canonical_url("https://idempotent.org/news/one")
    assert item_one is not None
    assert item_one.summary == "Updated summary one with new details"
