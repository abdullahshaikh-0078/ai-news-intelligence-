from datetime import datetime, timezone
import time
from typing import Any, Dict, List, Optional
from uuid import uuid4
import feedparser
from dateutil import parser as date_parser

from app.core.logging import logger
from app.domain.models.content_item import ContentType
from app.domain.models.source import SourceType
from app.domain.schemas.ingestion import NormalizedArticle
from app.infrastructure.models.source import Source
from app.ingestion.base import BaseIngestionAdapter
from app.ingestion.canonicalizer import canonicalize_url, generate_content_hash
from app.ingestion.http_client import FeedHttpClient


def parse_datetime(entry: Any) -> datetime:
    """Extract publication timestamp from parsed feed entry with safe UTC fallback."""
    # 1. Check struct_time from feedparser
    for time_attr in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed_time = getattr(entry, time_attr, None)
        if parsed_time:
            try:
                dt = datetime.fromtimestamp(time.mktime(parsed_time), tz=timezone.utc)
                return dt
            except Exception:
                pass

    # 2. Check string attributes with dateutil
    for str_attr in ("published", "pubDate", "updated", "created", "dc_date"):
        date_str = getattr(entry, str_attr, None) or (entry.get(str_attr) if isinstance(entry, dict) else None)
        if date_str and isinstance(date_str, str):
            try:
                dt = date_parser.parse(date_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                pass

    # Fallback to current UTC time if no dates are provided
    return datetime.now(timezone.utc)


class RSSAdapter(BaseIngestionAdapter):
    """Production-quality RSS 2.0 and Atom XML feed ingestion adapter."""

    def __init__(self, http_client: Optional[FeedHttpClient] = None):
        self.http_client = http_client or FeedHttpClient()

    @property
    def source_type(self) -> SourceType:
        return SourceType.RSS

    def parse_feed_content(self, raw_xml: str, source: Source) -> List[NormalizedArticle]:
        """Parse raw XML string into validated NormalizedArticle instances."""
        parsed = feedparser.parse(raw_xml)
        if parsed.bozo and not parsed.entries:
            # Bozo flag is set on XML errors; if no entries could be recovered, log and exit
            logger.warning(
                f"Failed to parse feed XML for source '{source.name}' [url={source.url}]: {parsed.bozo_exception}"
            )
            return []

        articles: List[NormalizedArticle] = []
        now_utc = datetime.now(timezone.utc)

        for entry in parsed.entries:
            try:
                # 1. Title extraction
                title = getattr(entry, "title", "").strip()
                if not title:
                    logger.debug(f"Skipping entry from source '{source.name}' without title")
                    continue

                # 2. Link & Canonical URL extraction
                raw_link = getattr(entry, "link", None)
                if not raw_link and hasattr(entry, "links") and entry.links:
                    for l in entry.links:
                        if l.get("rel") in ("alternate", None) and l.get("href"):
                            raw_link = l.get("href")
                            break
                    if not raw_link and entry.links[0].get("href"):
                        raw_link = entry.links[0].get("href")

                if not raw_link:
                    logger.debug(f"Skipping entry '{title[:30]}' from source '{source.name}' without link")
                    continue

                canon_url = canonicalize_url(raw_link)
                if not canon_url:
                    continue

                # 3. Summary / Excerpt extraction
                summary = getattr(entry, "summary", None) or getattr(entry, "description", None)
                if summary:
                    summary = summary.strip()

                # 4. Full Content body extraction if present
                raw_content = None
                if hasattr(entry, "content") and entry.content:
                    raw_content = entry.content[0].get("value")
                if not raw_content and hasattr(entry, "content_encoded"):
                    raw_content = entry.content_encoded
                if not raw_content:
                    raw_content = summary

                # 5. Author extraction
                author = getattr(entry, "author", None)
                if not author and hasattr(entry, "author_detail") and entry.author_detail:
                    author = getattr(entry.author_detail, "name", None)

                # 6. Publication Date
                published_at = parse_datetime(entry)

                # 7. External GUID / ID
                external_id = (
                    getattr(entry, "id", None)
                    or getattr(entry, "guid", None)
                    or (entry.get("id") if isinstance(entry, dict) else None)
                )

                # 8. Categories / Tags
                categories: List[str] = []
                if hasattr(entry, "tags") and entry.tags:
                    for t in entry.tags:
                        term = t.get("term") or t.get("label")
                        if term and isinstance(term, str):
                            categories.append(term.strip())
                elif hasattr(entry, "category") and entry.category:
                    categories.append(str(entry.category).strip())

                # 9. Deterministic content hash
                content_hash = generate_content_hash(
                    canonical_url=canon_url,
                    title=title,
                    content=raw_content or summary or "",
                )

                # 10. Raw metadata preservation
                raw_metadata: Dict[str, Any] = {
                    "feed_version": getattr(parsed, "version", "unknown"),
                    "categories": categories,
                    "original_link": raw_link,
                }
                thumbnail_url: Optional[str] = None
                if hasattr(entry, "media_thumbnail") and entry.media_thumbnail and isinstance(entry.media_thumbnail, list):
                    thumbnail_url = entry.media_thumbnail[0].get("url")
                elif hasattr(entry, "media_content") and entry.media_content and isinstance(entry.media_content, list):
                    thumbnail_url = entry.media_content[0].get("url")
                elif hasattr(entry, "enclosures") and entry.enclosures:
                    for enc in entry.enclosures:
                        if enc.get("type", "").startswith("image/"):
                            thumbnail_url = enc.get("href")
                            break

                if thumbnail_url:
                    raw_metadata["thumbnail_url"] = thumbnail_url

                articles.append(
                    NormalizedArticle(
                        source_id=source.id or uuid4(),
                        canonical_url=canon_url,
                        title=title,
                        summary=summary,
                        raw_content=raw_content,
                        author=author,
                        published_at=published_at,
                        fetched_at=now_utc,
                        external_id=str(external_id) if external_id else None,
                        language=source.language or "en",
                        content_hash=content_hash,
                        content_type=ContentType.ARTICLE,
                        thumbnail_url=thumbnail_url,
                        categories=categories,
                        metadata_json=raw_metadata,
                    )
                )

            except Exception as exc:
                logger.warning(
                    f"Error parsing entry from source '{source.name}': {str(exc)}"
                )
                continue

        return articles

    async def fetch_and_parse(self, source: Source) -> List[NormalizedArticle]:
        """Fetch remote feed and return parsed articles."""
        raw_xml = await self.http_client.fetch(source.url)
        return self.parse_feed_content(raw_xml, source)
