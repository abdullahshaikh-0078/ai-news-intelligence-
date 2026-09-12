from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin
from uuid import uuid4
import warnings
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from dateutil import parser as date_parser

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from app.core.logging import logger
from app.domain.models.content_item import ContentType
from app.domain.models.source import SourceType
from app.domain.schemas.ingestion import NormalizedArticle
from app.infrastructure.models.source import Source
from app.ingestion.adapters.rss_adapter import RSSAdapter
from app.ingestion.base import BaseIngestionAdapter
from app.ingestion.canonicalizer import canonicalize_url, generate_content_hash
from app.ingestion.http_client import FeedHttpClient


class AnthropicNewsParser:
    """Specialized HTML parser for Anthropic official news and announcements."""

    @staticmethod
    def parse(html: str, source: Source) -> List[NormalizedArticle]:
        soup = BeautifulSoup(html, "html.parser")
        articles: List[NormalizedArticle] = []
        seen_links = set()
        now_utc = datetime.now(timezone.utc)

        # In Anthropic /news, articles are linked via <a href="/news/...">
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            # Match article routes (excluding root /news)
            if not re.match(r"^/news/[a-zA-Z0-9-]+$", href) or href == "/news":
                continue
            if href in seen_links:
                continue

            text_content = " ".join(a_tag.stripped_strings)
            if len(text_content) < 5:
                continue

            # 1. Title extraction
            heading = a_tag.find(["h1", "h2", "h3", "h4"])
            title = heading.get_text(strip=True) if heading else None

            if not title:
                # Inspect parent container for heading
                parent = a_tag.parent
                for _ in range(3):
                    if parent:
                        parent_heading = parent.find(["h1", "h2", "h3", "h4"])
                        if parent_heading:
                            title = parent_heading.get_text(strip=True)
                            break
                        parent = parent.parent

            if not title:
                # Clean text fallback
                title = text_content[:120]

            # 2. Canonical link
            full_url = urljoin(source.url, href)
            canon_url = canonicalize_url(full_url)
            if not canon_url:
                continue

            # 3. Publication date extraction
            date_match = re.search(
                r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b",
                text_content,
            )
            pub_date = now_utc
            if date_match:
                try:
                    parsed_dt = date_parser.parse(date_match.group(0))
                    if parsed_dt.tzinfo is None:
                        parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
                    pub_date = parsed_dt
                except Exception:
                    pass

            # 4. Category extraction
            cat_match = re.search(r"\b(Announcements|Product|Research|Company|Safety|Policy)\b", text_content)
            category = cat_match.group(1) if cat_match else "Announcements"

            # 5. Summary extraction
            summary_text = None
            if heading and heading.parent:
                p_tag = heading.parent.find("p")
                if p_tag:
                    summary_text = p_tag.get_text(strip=True)
            if not summary_text:
                summary_text = text_content[:250]

            # 6. Content fingerprint hash
            content_hash = generate_content_hash(
                canonical_url=canon_url,
                title=title,
                content=summary_text or "",
            )

            seen_links.add(href)
            img = a_tag.find("img")
            thumb_url = urljoin("https://www.anthropic.com", img["src"]) if (img and img.get("src")) else None
            metadata = {
                "organization": "Anthropic",
                "parser": "anthropic_html",
                "original_path": href,
            }
            if thumb_url:
                metadata["thumbnail_url"] = thumb_url

            articles.append(
                NormalizedArticle(
                    source_id=source.id or uuid4(),
                    canonical_url=canon_url,
                    title=title,
                    summary=summary_text,
                    raw_content=summary_text,
                    author="Anthropic",
                    published_at=pub_date,
                    fetched_at=now_utc,
                    external_id=href,
                    language=source.language or "en",
                    content_hash=content_hash,
                    content_type=ContentType.ARTICLE,
                    thumbnail_url=thumb_url,
                    categories=[category],
                    metadata_json=metadata,
                )
            )

        return articles


class GenericHTMLCardParser:
    """Robust fallback parser extracting articles from semantic HTML structures."""

    @staticmethod
    def parse(html: str, source: Source) -> List[NormalizedArticle]:
        soup = BeautifulSoup(html, "html.parser")
        articles: List[NormalizedArticle] = []
        seen_urls = set()
        now_utc = datetime.now(timezone.utc)

        # Look for <article> containers or cards with links & headings
        candidates = soup.find_all("article")
        if not candidates:
            candidates = soup.find_all(["div", "section"], class_=re.compile(r"(card|post|article|item|entry)", re.I))

        for el in candidates:
            a_tag = el.find("a", href=True)
            if not a_tag:
                continue

            href = a_tag["href"].strip()
            if href.startswith(("#", "javascript:", "mailto:")):
                continue

            full_url = urljoin(source.url, href)
            canon_url = canonicalize_url(full_url)
            if not canon_url or canon_url in seen_urls:
                continue

            # Heading
            heading = el.find(["h1", "h2", "h3", "h4"]) or a_tag
            title = heading.get_text(strip=True)
            if not title or len(title) < 5:
                continue

            # Paragraph summary
            p_tag = el.find("p")
            summary = p_tag.get_text(strip=True) if p_tag else None

            # Date search
            time_tag = el.find("time")
            pub_date = now_utc
            if time_tag and (time_tag.get("datetime") or time_tag.get_text()):
                raw_d = time_tag.get("datetime") or time_tag.get_text()
                try:
                    dt = date_parser.parse(raw_d)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    pub_date = dt
                except Exception:
                    pass

            content_hash = generate_content_hash(
                canonical_url=canon_url,
                title=title,
                content=summary or "",
            )

            seen_urls.add(canon_url)
            img = el.find("img")
            thumb_url = urljoin(source.url, img["src"]) if (img and img.get("src")) else None
            metadata = {
                "parser": "generic_html_card",
            }
            if thumb_url:
                metadata["thumbnail_url"] = thumb_url

            articles.append(
                NormalizedArticle(
                    source_id=source.id or uuid4(),
                    canonical_url=canon_url,
                    title=title,
                    summary=summary,
                    raw_content=summary,
                    author=source.name,
                    published_at=pub_date,
                    fetched_at=now_utc,
                    external_id=canon_url,
                    language=source.language or "en",
                    content_hash=content_hash,
                    content_type=ContentType.ARTICLE,
                    thumbnail_url=thumb_url,
                    categories=[],
                    metadata_json=metadata,
                )
            )

        return articles


class OfficialWebAdapter(BaseIngestionAdapter):
    """Reusable adapter for authoritative first-party AI organization sources."""

    def __init__(self, http_client: Optional[FeedHttpClient] = None):
        self.http_client = http_client or FeedHttpClient()
        self.rss_adapter = RSSAdapter(self.http_client)

    @property
    def source_type(self) -> SourceType:
        return SourceType.WEB

    async def fetch_and_parse(self, source: Source) -> List[NormalizedArticle]:
        """Ingest official organization content prioritizing official feeds, falling back to structured HTML."""
        # 1. If official structured feed URL is configured, use it first (Reliability Rule 1)
        feed_url = source.config.get("feed_url") if isinstance(source.config, dict) else None
        if feed_url:
            logger.info(f"Using official structured feed for source '{source.name}' at {feed_url}")
            try:
                feed_xml = await self.http_client.fetch(feed_url)
                articles = self.rss_adapter.parse_feed_content(feed_xml, source)
                if articles:
                    return articles
            except Exception as exc:
                logger.warning(
                    f"Official feed fetch failed for '{source.name}' at {feed_url}: {str(exc)}. Falling back to HTML."
                )

        # 2. Fetch HTML page
        html = await self.http_client.fetch(source.url)

        # 3. Strategy selection based on configuration or source slug
        parser_type = (source.config.get("parser_type") if isinstance(source.config, dict) else "") or ""
        slug = (source.slug or "").lower()

        if parser_type == "anthropic" or "anthropic" in slug:
            return AnthropicNewsParser.parse(html, source)

        # Default fallback to generic card extraction
        return GenericHTMLCardParser.parse(html, source)
