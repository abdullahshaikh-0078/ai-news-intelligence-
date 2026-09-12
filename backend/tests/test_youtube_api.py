import pytest
from httpx import AsyncClient

YOUTUBE_PLAYLIST_MOCK = {
    "items": [
        {
            "contentDetails": {"videoId": "yt_api_vid_1"},
            "snippet": {"title": "State of AI Video"},
        }
    ]
}

YOUTUBE_VIDEOS_MOCK = {
    "items": [
        {
            "id": "yt_api_vid_1",
            "snippet": {
                "title": "State of AI in 2026",
                "description": "Deep dive into frontier models and multimodal breakthroughs.",
                "channelId": "UCbfYPyITQ-7l4upoX8nvctg",
                "channelTitle": "Two Minute Papers",
                "publishedAt": "2026-09-08T12:00:00Z",
                "tags": ["AI", "Research"],
                "categoryId": "28",
                "thumbnails": {
                    "high": {"url": "https://img.youtube.com/vi/yt_api_vid_1/hqdefault.jpg"}
                },
            },
            "contentDetails": {"duration": "PT10M20S"},
            "statistics": {
                "viewCount": "84000",
                "likeCount": "6200",
                "commentCount": "340",
            },
        }
    ]
}


@pytest.mark.asyncio
async def test_api_trigger_youtube_batch_ingestion(async_client: AsyncClient, monkeypatch):
    """Test POST /api/v1/ingestion/youtube batch endpoint."""
    monkeypatch.setattr("app.core.config.settings.YOUTUBE_API_KEY", "mock_key")
    monkeypatch.setattr("app.ingestion.adapters.youtube_adapter.settings.YOUTUBE_API_KEY", "mock_key")

    await async_client.post("/api/v1/sources/seed")

    async def mock_fetch_json(self, url, headers=None, params=None):
        if "playlistItems" in url:
            return YOUTUBE_PLAYLIST_MOCK
        elif "videos" in url:
            return YOUTUBE_VIDEOS_MOCK
        return {}

    monkeypatch.setattr("app.ingestion.http_client.FeedHttpClient.fetch_json", mock_fetch_json)

    response = await async_client.post("/api/v1/ingestion/youtube")
    assert response.status_code == 200
    data = response.json()
    assert data["total_sources"] >= 3
    assert data["succeeded_sources"] >= 1
    assert data["total_inserted"] >= 1


@pytest.mark.asyncio
async def test_api_trigger_single_youtube_source(async_client: AsyncClient, monkeypatch):
    """Test POST /api/v1/ingestion/sources/{source_id} for a YouTube source."""
    monkeypatch.setattr("app.core.config.settings.YOUTUBE_API_KEY", "mock_key")
    monkeypatch.setattr("app.ingestion.adapters.youtube_adapter.settings.YOUTUBE_API_KEY", "mock_key")

    seed_res = await async_client.post("/api/v1/sources/seed")
    sources = seed_res.json()
    yt_src = next((s for s in sources if s["slug"] == "two-minute-papers"), None)
    assert yt_src is not None

    async def mock_fetch_json(self, url, headers=None, params=None):
        if "playlistItems" in url:
            return YOUTUBE_PLAYLIST_MOCK
        elif "videos" in url:
            return YOUTUBE_VIDEOS_MOCK
        return {}

    monkeypatch.setattr("app.ingestion.http_client.FeedHttpClient.fetch_json", mock_fetch_json)

    response = await async_client.post(f"/api/v1/ingestion/sources/{yt_src['id']}")
    assert response.status_code == 200
    data = response.json()
    assert data["source_id"] == yt_src["id"]
    assert data["status"] == "SUCCESS"
    assert data["entries_fetched"] == 1
    assert data["entries_inserted"] == 1
