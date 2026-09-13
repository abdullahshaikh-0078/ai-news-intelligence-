import pytest
from httpx import AsyncClient

ITEM_MOCK = {
    "id": 41888888,
    "type": "story",
    "by": "ai_researcher",
    "time": 1725840000,
    "title": "Scaling Laws for Frontier AI Systems",
    "url": "https://example.com/scaling-laws",
    "score": 210,
    "descendants": 88,
}


@pytest.mark.asyncio
async def test_api_trigger_hacker_news_batch_ingestion(async_client: AsyncClient, monkeypatch):
    """Test POST /api/v1/ingestion/hacker-news batch endpoint."""
    await async_client.post("/api/v1/sources/seed")

    async def mock_fetch_json(self, url):
        if "topstories.json" in url or "beststories.json" in url:
            return [41888888]
        elif "item/41888888.json" in url:
            return ITEM_MOCK
        return None

    monkeypatch.setattr("app.ingestion.http_client.FeedHttpClient.fetch_json", mock_fetch_json)

    response = await async_client.post("/api/v1/ingestion/hacker-news")
    assert response.status_code == 200
    data = response.json()
    assert data["total_sources"] >= 1
    assert data["succeeded_sources"] >= 1
    assert data["total_inserted"] >= 1


@pytest.mark.asyncio
async def test_api_trigger_single_hacker_news_source(async_client: AsyncClient, monkeypatch):
    """Test POST /api/v1/ingestion/sources/{source_id} for Hacker News source."""
    seed_res = await async_client.post("/api/v1/sources/seed")
    sources = seed_res.json()
    hn_src = next((s for s in sources if s["slug"] == "hacker-news"), None)
    assert hn_src is not None

    async def mock_fetch_json(self, url):
        if "topstories.json" in url or "beststories.json" in url:
            return [41888888]
        elif "item/41888888.json" in url:
            return ITEM_MOCK
        return None

    monkeypatch.setattr("app.ingestion.http_client.FeedHttpClient.fetch_json", mock_fetch_json)

    response = await async_client.post(f"/api/v1/ingestion/sources/{hn_src['id']}")
    assert response.status_code == 200
    data = response.json()
    assert data["source_id"] == hn_src["id"]
    assert data["status"] == "SUCCESS"
    assert data["entries_fetched"] >= 1
