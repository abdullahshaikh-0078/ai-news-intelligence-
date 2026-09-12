import asyncio
from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse
from uuid import uuid4
from bs4 import BeautifulSoup

from app.core.logging import logger
from app.domain.models.content_item import ContentType
from app.domain.models.source import SourceType
from app.domain.schemas.ingestion import NormalizedArticle
from app.infrastructure.models.source import Source
from app.ingestion.base import BaseIngestionAdapter
from app.ingestion.canonicalizer import canonicalize_url, generate_content_hash
from app.ingestion.http_client import FeedHttpClient

DEFAULT_AI_KEYWORDS = [
    "ai",
    "llm",
    "gpt",
    "claude",
    "gemini",
    "deepseek",
    "openai",
    "anthropic",
    "machine learning",
    "deep learning",
    "neural",
    "transformer",
    "diffusion",
    "agent",
    "pytorch",
    "huggingface",
    "cuda",
    "gpu",
    "inference",
    "nlp",
    "vision",
    "speech",
    "embedding",
    "model",
    "prompt",
    "reasoning",
    "benchmark",
]


def strip_html(html_text: Optional[str]) -> str:
    """Extract clean text content from HTML markup."""
    if not html_text:
        return ""
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        text = soup.get_text(separator=" ", strip=True)
        text = re.sub(r"\s+([?.!,;:])", r"\1", text)
        return re.sub(r"\s+", " ", text).strip()
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html_text)
        text = re.sub(r"\s+([?.!,;:])", r"\1", text)
        return re.sub(r"\s+", " ", text).strip()


def is_ai_relevant(title: str, url: Optional[str], score: int, config: Dict[str, Any]) -> bool:
    """
    Deterministic AI keyword matching.
    Uses word boundaries for short acronyms to avoid false positives (e.g. 'paid', 'chain').
    """
    filter_enabled = config.get("filter_ai_only", True)
    if not filter_enabled:
        return True

    min_score = int(config.get("min_score", 0))
    if score < min_score:
        return False

    include_keywords = config.get("include_keywords", DEFAULT_AI_KEYWORDS)
    exclude_keywords = config.get("exclude_keywords", ["crypto", "bitcoin", "solana", "nft", "ethereum"])

    search_target = f"{title} {url or ''}".lower()

    # Check exclude keywords
    for ex in exclude_keywords:
        if ex.lower() in search_target:
            return False

    # Check include keywords
    for kw in include_keywords:
        kw_clean = kw.lower().strip()
        if not kw_clean:
            continue
        # For short tokens (<= 4 chars), use word boundaries
        if len(kw_clean) <= 4:
            pattern = rf"\b{re.escape(kw_clean)}\b"
            if re.search(pattern, search_target):
                return True
        else:
            if kw_clean in search_target:
                return True

    return False


class HackerNewsAdapter(BaseIngestionAdapter):
    """
    Production-quality ingestion adapter for the official Hacker News Firebase API.
    Retrieves top/best/new stories, applies deterministic AI relevance filtering,
    and normalizes items into canonical ContentItem representations.
    """

    DEFAULT_API_BASE = "https://hacker-news.firebaseio.com/v0"
    CONCURRENCY_LIMIT = 15

    def __init__(self, http_client: Optional[FeedHttpClient] = None):
        self.http_client = http_client or FeedHttpClient()

    @property
    def source_type(self) -> SourceType:
        return SourceType.WEB

    def normalize_story(self, item: Dict[str, Any], source: Source) -> Optional[NormalizedArticle]:
        """Normalize raw Hacker News item JSON into NormalizedArticle."""
        if not isinstance(item, dict):
            return None

        # 1. Filter out dead, deleted, or non-story items
        if item.get("deleted") or item.get("dead"):
            return None
        if item.get("type") != "story":
            return None

        hn_id = item.get("id")
        if not hn_id:
            return None

        title = item.get("title", "").strip()
        if not title:
            return None

        raw_url = item.get("url")
        score = int(item.get("score", 0))
        comments_count = int(item.get("descendants", 0))
        config = source.config if isinstance(source.config, dict) else {}

        # 2. AI Relevance Filter
        if not is_ai_relevant(title, raw_url, score, config):
            return None

        # 3. Canonical URL & External Links Strategy
        hn_url = f"https://news.ycombinator.com/item?id={hn_id}"
        if raw_url:
            canonical_url = canonicalize_url(raw_url)
            article_url = canonical_url
            try:
                domain = urlparse(canonical_url).netloc.lower()
            except Exception:
                domain = "unknown"
        else:
            # Self-post (Ask HN, Show HN without external URL)
            canonical_url = hn_url
            article_url = None
            domain = "news.ycombinator.com"

        # 4. Text & Summary
        raw_text = item.get("text")
        clean_text = strip_html(raw_text) if raw_text else None

        # 5. Publication timestamp
        raw_time = item.get("time")
        if raw_time:
            try:
                published_at = datetime.fromtimestamp(raw_time, tz=timezone.utc)
            except Exception:
                published_at = datetime.now(timezone.utc)
        else:
            published_at = datetime.now(timezone.utc)

        # 6. Author
        author = item.get("by")

        # 7. Content Hash
        content_hash = generate_content_hash(title, clean_text or canonical_url)

        # 8. Rich Community Metadata
        metadata_json = {
            "hn_id": hn_id,
            "hn_url": hn_url,
            "article_url": article_url,
            "score": score,
            "comments": comments_count,
            "domain": domain,
            "item_type": "story",
            "by": author,
            "community": "Hacker News",
        }

        return NormalizedArticle(
            source_id=source.id or uuid4(),
            canonical_url=canonical_url,
            title=title,
            summary=clean_text,
            raw_content=clean_text,
            author=author,
            published_at=published_at,
            fetched_at=datetime.now(timezone.utc),
            external_id=f"hn:{hn_id}",
            language=source.language or "en",
            content_hash=content_hash,
            content_type=ContentType.COMMUNITY_POST,
            thumbnail_url=None,
            categories=["Community", "Developer", "Hacker News"],
            metadata_json=metadata_json,
        )

    async def fetch_and_parse(self, source: Source) -> List[NormalizedArticle]:
        """
        Fetch top/best story IDs from the official Hacker News API and resolve items concurrently.
        """
        config = source.config if isinstance(source.config, dict) else {}
        api_base = config.get("api_base_url", self.DEFAULT_API_BASE).rstrip("/")
        feeds = config.get("feeds", ["topstories"])
        max_items_per_feed = int(config.get("max_items_per_feed", 50))

        # 1. Fetch story IDs across configured feeds
        story_ids: List[int] = []
        seen_ids: Set[int] = set()

        for feed_name in feeds:
            feed_url = f"{api_base}/{feed_name}.json"
            try:
                feed_data = await self.http_client.fetch_json(feed_url)
                if isinstance(feed_data, list):
                    for sid in feed_data[:max_items_per_feed]:
                        if isinstance(sid, int) and sid not in seen_ids:
                            seen_ids.add(sid)
                            story_ids.append(sid)
            except Exception as exc:
                logger.warning(
                    f"Failed to fetch Hacker News feed '{feed_name}' from {feed_url}: {str(exc)}"
                )

        logger.info(
            f"Discovered {len(story_ids)} unique Hacker News story IDs from feeds {feeds} for '{source.name}'"
        )
        if not story_ids:
            return []

        # 2. Concurrent item resolution with bounded semaphore
        semaphore = asyncio.Semaphore(self.CONCURRENCY_LIMIT)

        async def fetch_item(item_id: int) -> Optional[Dict[str, Any]]:
            item_url = f"{api_base}/item/{item_id}.json"
            async with semaphore:
                try:
                    return await self.http_client.fetch_json(item_url)
                except Exception as exc:
                    logger.debug(f"Failed to fetch HN item {item_id}: {str(exc)}")
                    return None

        tasks = [fetch_item(sid) for sid in story_ids]
        raw_items = await asyncio.gather(*tasks)

        # 3. Normalize and filter items
        articles: List[NormalizedArticle] = []
        for raw_item in raw_items:
            if not raw_item:
                continue
            normalized = self.normalize_story(raw_item, source)
            if normalized:
                articles.append(normalized)

        logger.info(
            f"Hacker News ingestion complete for '{source.name}': {len(raw_items)} items fetched, {len(articles)} AI stories accepted"
        )
        return articles
