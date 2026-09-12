import pytest
from httpx import AsyncClient

SAMPLE_OPENAI_FEED = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
<channel>
    <title>OpenAI News</title>
    <link>https://openai.com</link>
    <item>
        <title>API Test Article</title>
        <link>https://openai.com/index/api-test-article</link>
        <description>API Description</description>
    </item>
</channel>
</rss>
"""


@pytest.mark.asyncio
async def test_api_trigger_official_batch_ingestion(async_client: AsyncClient, monkeypatch):
    """Verify POST /api/v1/ingestion/official triggers batch ingestion for official sources."""
    # Ensure baseline sources are seeded
    await async_client.post("/api/v1/sources/seed")

    async def mock_fetch(self, url):
        return SAMPLE_OPENAI_FEED

    monkeypatch.setattr("app.ingestion.http_client.FeedHttpClient.fetch", mock_fetch)

    response = await async_client.post("/api/v1/ingestion/official")
    assert response.status_code == 200
    data = response.json()
    assert "total_sources" in data
    assert data["total_sources"] >= 3
    assert data["succeeded_sources"] >= 3
    assert data["total_inserted"] >= 1


@pytest.mark.asyncio
async def test_api_trigger_single_official_source(async_client: AsyncClient, monkeypatch):
    """Verify POST /api/v1/ingestion/sources/{id} runs for an official WEB source."""
    seed_res = await async_client.post("/api/v1/sources/seed")
    sources = seed_res.json()
    anthropic_src = next((s for s in sources if s["slug"] == "anthropic-news"), None)
    assert anthropic_src is not None

    async def mock_fetch(self, url):
        return """<html><body><a href="/news/api-test-claude"><h3>Claude API Update</h3></a></body></html>"""

    monkeypatch.setattr("app.ingestion.http_client.FeedHttpClient.fetch", mock_fetch)

    response = await async_client.post(f"/api/v1/ingestion/sources/{anthropic_src['id']}")
    assert response.status_code == 200
    data = response.json()
    assert data["source_id"] == anthropic_src["id"]
    assert data["status"] == "SUCCESS"
    assert data["entries_fetched"] >= 1
