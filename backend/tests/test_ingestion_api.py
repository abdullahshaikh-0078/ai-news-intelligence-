import pytest
import uuid
from httpx import AsyncClient

SAMPLE_API_FEED = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
<channel>
    <title>API Test Feed</title>
    <link>https://api-feed.org</link>
    <item>
        <title>API Article 1</title>
        <link>https://api-feed.org/post-1</link>
        <description>Description 1</description>
    </item>
</channel>
</rss>
"""


@pytest.mark.asyncio
async def test_trigger_rss_ingestion_api(async_client: AsyncClient, monkeypatch):
    """Verify POST /api/v1/ingestion/rss returns aggregated summary."""
    async def mock_fetch(self, url):
        return SAMPLE_API_FEED

    monkeypatch.setattr("app.ingestion.http_client.FeedHttpClient.fetch", mock_fetch)

    # 1. Register an RSS source
    tag = uuid.uuid4().hex[:6]
    create_res = await async_client.post(
        "/api/v1/sources",
        json={"name": f"API RSS {tag}", "type": "RSS", "url": f"https://api-feed-{tag}.org/rss.xml", "enabled": True},
    )
    assert create_res.status_code == 201

    # 2. Trigger batch RSS ingestion
    response = await async_client.post("/api/v1/ingestion/rss")
    assert response.status_code == 200
    data = response.json()
    assert "total_sources" in data
    assert "total_inserted" in data
    assert data["succeeded_sources"] >= 1
    assert data["total_inserted"] >= 1


@pytest.mark.asyncio
async def test_trigger_single_source_ingestion_api(async_client: AsyncClient, monkeypatch):
    """Verify POST /api/v1/ingestion/sources/{id} runs ingestion for one source."""
    async def mock_fetch(self, url):
        return SAMPLE_API_FEED

    monkeypatch.setattr("app.ingestion.http_client.FeedHttpClient.fetch", mock_fetch)

    tag = uuid.uuid4().hex[:6]
    create_res = await async_client.post(
        "/api/v1/sources",
        json={"name": f"Single Source {tag}", "type": "RSS", "url": f"https://single-{tag}.org/feed", "enabled": True},
    )
    source_id = create_res.json()["id"]

    response = await async_client.post(f"/api/v1/ingestion/sources/{source_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["source_id"] == source_id
    assert data["status"] == "SUCCESS"
    assert data["entries_fetched"] == 1
    assert data["entries_inserted"] == 1


@pytest.mark.asyncio
async def test_trigger_source_not_found_api(async_client: AsyncClient):
    """Verify 404 is returned when triggering ingestion for non-existent source ID."""
    random_id = uuid.uuid4()
    response = await async_client.post(f"/api/v1/ingestion/sources/{random_id}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
