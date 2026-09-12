from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from app.core.config import settings
from app.core.exceptions import AppException
from app.core.logging import logger
from app.domain.models.content_item import ContentType
from app.domain.models.source import SourceType
from app.domain.schemas.ingestion import NormalizedArticle
from app.infrastructure.models.source import Source
from app.ingestion.base import BaseIngestionAdapter
from app.ingestion.canonicalizer import generate_content_hash
from app.ingestion.http_client import FeedFetchError, FeedHttpClient


class YouTubeAdapterError(AppException):
    """Raised when YouTube API processing encounters an error."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message=message, code="YOUTUBE_ADAPTER_ERROR", status_code=status_code)


def extract_channel_id(url: str, config: Dict[str, Any]) -> Optional[str]:
    """Extract YouTube channel ID from configuration or source URL."""
    if config.get("channel_id"):
        return str(config["channel_id"]).strip()

    parsed = urlparse(url)
    # Match /channel/UC...
    channel_match = re.search(r"/channel/([a-zA-Z0-9_-]+)", parsed.path)
    if channel_match:
        return channel_match.group(1)

    # Match ?channel_id=UC...
    query_params = parse_qs(parsed.query)
    if "channel_id" in query_params:
        return query_params["channel_id"][0]

    return None


def derive_uploads_playlist_id(channel_id: str) -> Optional[str]:
    """Derive YouTube uploads playlist ID from channel ID (replacing leading 'UC' with 'UU')."""
    if channel_id and channel_id.startswith("UC") and len(channel_id) > 2:
        return f"UU{channel_id[2:]}"
    return None


def select_best_thumbnail(thumbnails: Dict[str, Any]) -> Optional[str]:
    """Select the highest resolution available thumbnail URL."""
    for quality in ("maxres", "standard", "high", "medium", "default"):
        thumb = thumbnails.get(quality)
        if isinstance(thumb, dict) and thumb.get("url"):
            return thumb["url"]
    return None


def parse_rfc3339_datetime(date_str: Optional[str]) -> datetime:
    """Parse YouTube RFC 3339 / ISO 8601 timestamp into a timezone-aware UTC datetime."""
    if not date_str:
        return datetime.now(timezone.utc)
    try:
        clean_str = date_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_str)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


class YouTubeAdapter(BaseIngestionAdapter):
    """Production-quality YouTube Data API v3 ingestion adapter.
    
    Uses a quota-efficient uploads playlist strategy:
    1. Resolve uploads playlist ID (cached, derived UU... or channels.list).
    2. playlistItems.list (1 unit quota) to fetch recent video IDs.
    3. videos.list (1 unit quota) to fetch metadata and statistics in one batch.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        http_client: Optional[FeedHttpClient] = None,
    ):
        self._api_key = api_key
        self.http_client = http_client or FeedHttpClient()
        self.base_url = "https://www.googleapis.com/youtube/v3"

    @property
    def api_key(self) -> Optional[str]:
        return self._api_key or settings.YOUTUBE_API_KEY

    @property
    def source_type(self) -> SourceType:
        return SourceType.YOUTUBE

    async def fetch_and_parse(self, source: Source) -> List[NormalizedArticle]:
        """Fetch recent channel videos via official YouTube Data API v3 and normalize them."""
        api_key = self.api_key
        if not api_key:
            raise YouTubeAdapterError(
                "YOUTUBE_API_KEY is not configured in settings or environment. Cannot perform YouTube ingestion.",
                status_code=401,
            )

        config = source.config if isinstance(source.config, dict) else {}
        channel_id = extract_channel_id(source.url, config)
        if not channel_id:
            raise YouTubeAdapterError(
                f"Source '{source.name}' missing valid channel_id in configuration or URL: {source.url}"
            )

        max_results = min(max(int(config.get("max_results", 10)), 1), 50)

        # Step 1: Determine uploads playlist ID
        uploads_playlist_id = config.get("uploads_playlist_id")
        if not uploads_playlist_id:
            # First try derivation convention UC... -> UU...
            derived_id = derive_uploads_playlist_id(channel_id)
            if derived_id and not config.get("force_channel_lookup"):
                uploads_playlist_id = derived_id
            else:
                uploads_playlist_id = await self._fetch_uploads_playlist_id(channel_id, api_key)

        if not uploads_playlist_id:
            uploads_playlist_id = await self._fetch_uploads_playlist_id(channel_id, api_key)

        # Step 2: Fetch recent video items from playlist
        video_ids = await self._fetch_playlist_video_ids(uploads_playlist_id, max_results, api_key)
        if not video_ids:
            logger.info(f"No videos found in uploads playlist '{uploads_playlist_id}' for channel '{channel_id}'")
            return []

        # Step 3: Batch fetch detailed metadata & statistics for all video IDs
        return await self._fetch_video_details(video_ids, source, channel_id, api_key)

    async def _fetch_uploads_playlist_id(self, channel_id: str, api_key: str) -> str:
        """Fetch uploads playlist ID from channels.list endpoint."""
        url = f"{self.base_url}/channels"
        headers = {"X-Goog-Api-Key": api_key}
        params = {"part": "contentDetails", "id": channel_id, "key": api_key}

        try:
            data = await self.http_client.fetch_json(url, headers=headers, params=params)
            items = data.get("items", [])
            if not items:
                raise YouTubeAdapterError(f"Channel not found or private for channel_id: '{channel_id}'")
            uploads_id = (
                items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
            )
            if not uploads_id:
                raise YouTubeAdapterError(f"No uploads playlist found for channel_id: '{channel_id}'")
            return uploads_id
        except FeedFetchError as exc:
            raise YouTubeAdapterError(f"Failed to fetch channel details for '{channel_id}': {str(exc)}") from exc

    async def _fetch_playlist_video_ids(
        self,
        uploads_playlist_id: str,
        max_results: int,
        api_key: str,
    ) -> List[str]:
        """Fetch recent video IDs from playlistItems.list endpoint."""
        url = f"{self.base_url}/playlistItems"
        headers = {"X-Goog-Api-Key": api_key}
        params = {
            "part": "snippet,contentDetails",
            "playlistId": uploads_playlist_id,
            "maxResults": max_results,
            "key": api_key,
        }

        try:
            data = await self.http_client.fetch_json(url, headers=headers, params=params)
            items = data.get("items", [])
            video_ids: List[str] = []
            for item in items:
                v_id = (
                    item.get("contentDetails", {}).get("videoId")
                    or item.get("snippet", {}).get("resourceId", {}).get("videoId")
                )
                if v_id and v_id not in video_ids:
                    video_ids.append(v_id)
            return video_ids
        except FeedFetchError as exc:
            raise YouTubeAdapterError(
                f"Failed to fetch playlist items for playlist '{uploads_playlist_id}': {str(exc)}"
            ) from exc

    async def _fetch_video_details(
        self,
        video_ids: List[str],
        source: Source,
        channel_id: str,
        api_key: str,
    ) -> List[NormalizedArticle]:
        """Fetch full video metadata, durations, and statistics via videos.list in a single batch."""
        url = f"{self.base_url}/videos"
        headers = {"X-Goog-Api-Key": api_key}
        params = {
            "part": "snippet,contentDetails,statistics",
            "id": ",".join(video_ids),
            "key": api_key,
        }

        try:
            data = await self.http_client.fetch_json(url, headers=headers, params=params)
        except FeedFetchError as exc:
            raise YouTubeAdapterError(f"Failed to fetch video details: {str(exc)}") from exc

        items = data.get("items", [])
        articles: List[NormalizedArticle] = []
        now_utc = datetime.now(timezone.utc)

        for item in items:
            v_id = item.get("id")
            if not v_id:
                continue

            snippet = item.get("snippet", {})
            content_details = item.get("contentDetails", {})
            statistics = item.get("statistics", {})

            title = snippet.get("title", "").strip()
            if not title:
                continue

            raw_description = snippet.get("description", "")
            # Short summary: first 500 characters of description
            summary = raw_description[:500].strip() if raw_description else None
            author = snippet.get("channelTitle", source.name).strip()
            published_at = parse_rfc3339_datetime(snippet.get("publishedAt"))

            canonical_url = f"https://www.youtube.com/watch?v={v_id}"
            external_id = f"youtube:{v_id}"

            thumbnails = snippet.get("thumbnails", {})
            best_thumb_url = select_best_thumbnail(thumbnails)

            # Statistics extraction (safe int conversion)
            view_count = int(statistics["viewCount"]) if "viewCount" in statistics and str(statistics["viewCount"]).isdigit() else None
            like_count = int(statistics["likeCount"]) if "likeCount" in statistics and str(statistics["likeCount"]).isdigit() else None
            comment_count = int(statistics["commentCount"]) if "commentCount" in statistics and str(statistics["commentCount"]).isdigit() else None

            duration = content_details.get("duration")
            tags = snippet.get("tags", [])
            category_id = snippet.get("categoryId")

            categories: List[str] = [t for t in tags[:10]]
            if category_id:
                categories.append(f"category:{category_id}")

            metadata_json: Dict[str, Any] = {
                "video_id": v_id,
                "channel_id": snippet.get("channelId", channel_id),
                "channel_title": author,
                "thumbnail_url": best_thumb_url,
                "thumbnails": thumbnails,
                "view_count": view_count,
                "like_count": like_count,
                "comment_count": comment_count,
                "duration": duration,
                "tags": tags,
                "category_id": category_id,
                "live_broadcast_content": snippet.get("liveBroadcastContent"),
            }

            content_hash = generate_content_hash(title, raw_description, author)
            language = snippet.get("defaultLanguage") or snippet.get("defaultAudioLanguage") or "en"

            articles.append(
                NormalizedArticle(
                    source_id=source.id or uuid4(),
                    canonical_url=canonical_url,
                    title=title,
                    summary=summary,
                    raw_content=raw_description,
                    author=author,
                    published_at=published_at,
                    fetched_at=now_utc,
                    external_id=external_id,
                    language=language,
                    content_hash=content_hash,
                    content_type=ContentType.VIDEO,
                    thumbnail_url=best_thumb_url,
                    categories=categories,
                    metadata_json=metadata_json,
                )
            )

        return articles
