from datetime import datetime, timezone
import pytest
from unittest.mock import AsyncMock

from app.domain.models.source import SourceType
from app.infrastructure.models.source import Source
from app.ingestion.adapters.youtube_adapter import (
    YouTubeAdapter,
    YouTubeAdapterError,
    derive_uploads_playlist_id,
    extract_channel_id,
    parse_rfc3339_datetime,
    select_best_thumbnail,
)
from app.ingestion.http_client import FeedHttpClient


def test_extract_channel_id():
    """Verify channel ID extraction from config and diverse URL formats."""
    # From config
    assert extract_channel_id("https://youtube.com", {"channel_id": "UC12345"}) == "UC12345"

    # From /channel/UC... URL
    assert (
        extract_channel_id("https://www.youtube.com/channel/UCbfYPyITQ-7l4upoX8nvctg", {})
        == "UCbfYPyITQ-7l4upoX8nvctg"
    )

    # From ?channel_id=UC... URL
    assert (
        extract_channel_id(
            "https://www.youtube.com/feeds/videos.xml?channel_id=UCEBm0xLn28l-H0V586dJ7qw",
            {},
        )
        == "UCEBm0xLn28l-H0V586dJ7qw"
    )

    # Missing
    assert extract_channel_id("https://example.com/invalid", {}) is None


def test_derive_uploads_playlist_id():
    """Verify UC -> UU uploads playlist derivation."""
    assert derive_uploads_playlist_id("UCbfYPyITQ-7l4upoX8nvctg") == "UUbfYPyITQ-7l4upoX8nvctg"
    assert derive_uploads_playlist_id("UCEBm0xLn28l-H0V586dJ7qw") == "UUEBm0xLn28l-H0V586dJ7qw"
    assert derive_uploads_playlist_id("invalid") is None
    assert derive_uploads_playlist_id("") is None


def test_parse_rfc3339_datetime():
    """Verify RFC 3339 datetime parsing."""
    dt = parse_rfc3339_datetime("2026-09-08T14:30:00Z")
    assert dt.year == 2026
    assert dt.month == 9
    assert dt.day == 8
    assert dt.hour == 14
    assert dt.minute == 30
    assert dt.tzinfo == timezone.utc

    # Fallback on None or malformed
    dt_none = parse_rfc3339_datetime(None)
    assert dt_none.tzinfo is not None

    dt_bad = parse_rfc3339_datetime("not-a-date")
    assert dt_bad.tzinfo is not None


def test_select_best_thumbnail():
    """Verify selection of highest resolution thumbnail."""
    thumbs = {
        "default": {"url": "https://img.youtube.com/default.jpg"},
        "high": {"url": "https://img.youtube.com/high.jpg"},
        "maxres": {"url": "https://img.youtube.com/maxres.jpg"},
    }
    assert select_best_thumbnail(thumbs) == "https://img.youtube.com/maxres.jpg"

    thumbs_partial = {
        "medium": {"url": "https://img.youtube.com/medium.jpg"},
    }
    assert select_best_thumbnail(thumbs_partial) == "https://img.youtube.com/medium.jpg"
    assert select_best_thumbnail({}) is None


@pytest.mark.asyncio
async def test_youtube_adapter_missing_api_key():
    """Verify adapter raises YouTubeAdapterError when API key is missing."""
    adapter = YouTubeAdapter(api_key=None)
    adapter._api_key = None
    # Ensure settings key is also overridden
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.ingestion.adapters.youtube_adapter.settings.YOUTUBE_API_KEY", None)
        source = Source(
            name="Two Minute Papers",
            slug="two-minute-papers",
            type=SourceType.YOUTUBE.value,
            url="https://www.youtube.com/channel/UCbfYPyITQ-7l4upoX8nvctg",
            config={"channel_id": "UCbfYPyITQ-7l4upoX8nvctg"},
        )
        with pytest.raises(YouTubeAdapterError) as exc_info:
            await adapter.fetch_and_parse(source)
        assert "YOUTUBE_API_KEY is not configured" in str(exc_info.value.message)


@pytest.mark.asyncio
async def test_youtube_adapter_fetch_and_parse_success():
    """Verify full successful playlist item fetch and batch video detail normalization."""
    mock_client = AsyncMock(spec=FeedHttpClient)

    # Mock playlistItems response
    playlist_response = {
        "items": [
            {
                "contentDetails": {"videoId": "vid_001"},
                "snippet": {"title": "Playlist Title 1"},
            },
            {
                "contentDetails": {"videoId": "vid_002"},
                "snippet": {"title": "Playlist Title 2"},
            },
        ]
    }

    # Mock videos.list response
    videos_response = {
        "items": [
            {
                "id": "vid_001",
                "snippet": {
                    "title": "OpenAI O3 Reasoning Model Explained!",
                    "description": "In this video we break down OpenAI O3.\nFull breakdown here.",
                    "channelId": "UCbfYPyITQ-7l4upoX8nvctg",
                    "channelTitle": "Two Minute Papers",
                    "publishedAt": "2026-09-08T12:00:00Z",
                    "defaultLanguage": "en",
                    "tags": ["AI", "OpenAI", "Deep Learning", "Reasoning"],
                    "categoryId": "28",
                    "thumbnails": {
                        "high": {"url": "https://i.ytimg.com/vi/vid_001/hqdefault.jpg"},
                        "maxres": {"url": "https://i.ytimg.com/vi/vid_001/maxresdefault.jpg"},
                    },
                },
                "contentDetails": {
                    "duration": "PT8M24S",
                },
                "statistics": {
                    "viewCount": "154200",
                    "likeCount": "12500",
                    "commentCount": "845",
                },
            },
            {
                "id": "vid_002",
                "snippet": {
                    "title": "Claude 3.7 Sonnet Hybrid Reasoning Architecture",
                    "description": "A deep dive into Anthropic Claude 3.7 hybrid mode.",
                    "channelId": "UCbfYPyITQ-7l4upoX8nvctg",
                    "channelTitle": "Two Minute Papers",
                    "publishedAt": "2026-09-07T10:00:00Z",
                    "tags": ["Anthropic", "Claude"],
                    "categoryId": "28",
                    "thumbnails": {
                        "high": {"url": "https://i.ytimg.com/vi/vid_002/hqdefault.jpg"},
                    },
                },
                "contentDetails": {
                    "duration": "PT14M10S",
                },
                "statistics": {
                    "viewCount": "98000",
                    "likeCount": "7200",
                    "commentCount": "410",
                },
            },
        ]
    }

    async def mock_fetch_json(url, headers=None, params=None):
        if "playlistItems" in url:
            return playlist_response
        elif "videos" in url:
            return videos_response
        return {}

    mock_client.fetch_json.side_effect = mock_fetch_json

    adapter = YouTubeAdapter(api_key="mock_test_key", http_client=mock_client)
    source = Source(
        name="Two Minute Papers",
        slug="two-minute-papers",
        type=SourceType.YOUTUBE.value,
        url="https://www.youtube.com/channel/UCbfYPyITQ-7l4upoX8nvctg",
        config={"channel_id": "UCbfYPyITQ-7l4upoX8nvctg", "max_results": 5},
    )

    articles = await adapter.fetch_and_parse(source)
    assert len(articles) == 2

    # Check first article
    art1 = articles[0]
    assert art1.title == "OpenAI O3 Reasoning Model Explained!"
    assert art1.canonical_url == "https://www.youtube.com/watch?v=vid_001"
    assert art1.external_id == "youtube:vid_001"
    assert art1.author == "Two Minute Papers"
    assert art1.language == "en"
    assert art1.published_at.year == 2026
    assert art1.summary == "In this video we break down OpenAI O3.\nFull breakdown here."
    assert "AI" in art1.categories
    assert "category:28" in art1.categories
    assert art1.metadata_json["video_id"] == "vid_001"
    assert art1.metadata_json["channel_id"] == "UCbfYPyITQ-7l4upoX8nvctg"
    assert art1.metadata_json["view_count"] == 154200
    assert art1.metadata_json["like_count"] == 12500
    assert art1.metadata_json["comment_count"] == 845
    assert art1.metadata_json["duration"] == "PT8M24S"
    assert art1.metadata_json["thumbnail_url"] == "https://i.ytimg.com/vi/vid_001/maxresdefault.jpg"

    # Check second article
    art2 = articles[1]
    assert art2.title == "Claude 3.7 Sonnet Hybrid Reasoning Architecture"
    assert art2.external_id == "youtube:vid_002"
    assert art2.metadata_json["view_count"] == 98000
    assert art2.metadata_json["thumbnail_url"] == "https://i.ytimg.com/vi/vid_002/hqdefault.jpg"


@pytest.mark.asyncio
async def test_youtube_adapter_partial_statistics():
    """Verify graceful handling of missing statistics and empty descriptions."""
    mock_client = AsyncMock(spec=FeedHttpClient)

    playlist_response = {
        "items": [{"contentDetails": {"videoId": "partial_vid"}}]
    }

    videos_response = {
        "items": [
            {
                "id": "partial_vid",
                "snippet": {
                    "title": "Video Without Stats",
                    "description": "",
                    "channelId": "UC123",
                    "channelTitle": "AI Channel",
                    "publishedAt": "2026-09-01T00:00:00Z",
                },
                # No contentDetails or statistics
            }
        ]
    }

    async def mock_fetch_json(url, headers=None, params=None):
        if "playlistItems" in url:
            return playlist_response
        return videos_response

    mock_client.fetch_json.side_effect = mock_fetch_json

    adapter = YouTubeAdapter(api_key="mock_key", http_client=mock_client)
    source = Source(
        name="AI Channel",
        slug="ai-channel",
        type=SourceType.YOUTUBE.value,
        url="https://www.youtube.com/channel/UC123",
        config={"channel_id": "UC123"},
    )

    articles = await adapter.fetch_and_parse(source)
    assert len(articles) == 1
    art = articles[0]
    assert art.title == "Video Without Stats"
    assert art.summary is None
    assert art.metadata_json["view_count"] is None
    assert art.metadata_json["like_count"] is None
    assert art.metadata_json["comment_count"] is None
    assert art.metadata_json["duration"] is None
