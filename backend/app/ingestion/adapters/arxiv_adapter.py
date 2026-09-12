import asyncio
from datetime import datetime, timezone
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode
from uuid import uuid4
import warnings
import feedparser
from dateutil import parser as date_parser

warnings.filterwarnings("ignore", category=DeprecationWarning, module="feedparser")

from app.core.logging import logger
from app.domain.models.content_item import ContentType
from app.domain.models.source import SourceType
from app.domain.schemas.ingestion import NormalizedArticle
from app.infrastructure.models.source import Source
from app.ingestion.base import BaseIngestionAdapter
from app.ingestion.canonicalizer import canonicalize_url, generate_content_hash
from app.ingestion.http_client import FeedHttpClient

# Matches modern (e.g. 2303.08774, 2501.12345v2) and legacy (e.g. cs/0112017v1, math.GT/0309136) identifiers
ARXIV_ID_PATTERN = re.compile(
    r"(?:https?://arxiv\.org/(?:abs|pdf)/)?([a-zA-Z-]+(?:\.[a-zA-Z-]+)?/\d+|\d{4}\.\d{4,5})(?:v(\d+))?(?:\.pdf)?",
    re.IGNORECASE,
)


def extract_arxiv_identity(raw_id: str) -> Tuple[str, Optional[str], str, str]:
    """
    Extract (base_id, version, canonical_url, pdf_url) from raw ArXiv identifier or link.

    Examples:
        'http://arxiv.org/abs/2303.08774v2' -> ('2303.08774', 'v2', 'https://arxiv.org/abs/2303.08774', 'https://arxiv.org/pdf/2303.08774.pdf')
        'cs/0112017v1' -> ('cs/0112017', 'v1', 'https://arxiv.org/abs/cs/0112017', 'https://arxiv.org/pdf/cs/0112017.pdf')
        'https://arxiv.org/abs/2501.12345' -> ('2501.12345', None, 'https://arxiv.org/abs/2501.12345', 'https://arxiv.org/pdf/2501.12345.pdf')
    """
    if not raw_id:
        return "", None, "", ""

    clean_raw = raw_id.strip()
    match = ARXIV_ID_PATTERN.search(clean_raw)
    if match:
        base_id = match.group(1)
        version = f"v{match.group(2)}" if match.group(2) else None
        canonical_url = f"https://arxiv.org/abs/{base_id}"
        pdf_url = f"https://arxiv.org/pdf/{base_id}.pdf"
        return base_id, version, canonical_url, pdf_url

    # Fallback to general canonicalization
    canon = canonicalize_url(clean_raw)
    return clean_raw, None, canon, ""


def normalize_whitespace(text: Optional[str]) -> str:
    """Normalize multiline whitespace and remove wrapping newlines typical of ArXiv Atom payloads."""
    if not text:
        return ""
    # Replace newlines with spaces and condense multiple spaces
    return re.sub(r"\s+", " ", text).strip()


def parse_datetime(entry: Any) -> datetime:
    """Extract publication timestamp with safe UTC fallback."""
    for time_attr in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed_time = getattr(entry, time_attr, None)
        if parsed_time:
            try:
                return datetime.fromtimestamp(time.mktime(parsed_time), tz=timezone.utc)
            except Exception:
                pass

    for str_attr in ("published", "pubDate", "updated", "created"):
        date_str = getattr(entry, str_attr, None) or (entry.get(str_attr) if isinstance(entry, dict) else None)
        if date_str and isinstance(date_str, str):
            try:
                dt = date_parser.parse(date_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except Exception:
                pass

    return datetime.now(timezone.utc)


class ArXivAdapter(BaseIngestionAdapter):
    """
    Production-grade ingestion adapter for official ArXiv API / Atom interface.
    Fetches AI/ML research papers according to configured search queries and categories,
    normalizing entries into canonical ContentItem instances.
    """

    DEFAULT_API_URL = "https://export.arxiv.org/api/query"
    DEFAULT_RATE_LIMIT_DELAY = 3.0  # Respectful rate limiting pause (seconds) per ArXiv guidelines

    def __init__(self, http_client: Optional[FeedHttpClient] = None):
        self.http_client = http_client or FeedHttpClient()

    @property
    def source_type(self) -> SourceType:
        return SourceType.ARXIV

    def parse_feed_content(self, raw_xml: str, source: Source) -> List[NormalizedArticle]:
        """Parse raw Atom XML string returned from ArXiv API into NormalizedArticle records."""
        parsed = feedparser.parse(raw_xml)
        if parsed.bozo and not parsed.entries:
            logger.warning(
                f"Failed to parse ArXiv Atom XML for source '{source.name}': {parsed.bozo_exception}"
            )
            return []

        articles: List[NormalizedArticle] = []
        now_utc = datetime.now(timezone.utc)

        for entry in parsed.entries:
            try:
                # 1. Raw ID and Identity Extraction
                raw_id = getattr(entry, "id", "") or entry.get("link", "")
                if not raw_id:
                    continue

                base_id, version, canonical_url, default_pdf_url = extract_arxiv_identity(raw_id)
                if not canonical_url:
                    continue

                # 2. Title extraction & normalization
                raw_title = getattr(entry, "title", "")
                title = normalize_whitespace(raw_title)
                if not title:
                    continue

                # 3. Abstract / Summary
                raw_summary = getattr(entry, "summary", "")
                abstract = normalize_whitespace(raw_summary)

                # 4. Publication & Updated Dates
                published_at = parse_datetime(entry)
                updated_at_val = None
                if hasattr(entry, "updated_parsed") or "updated" in entry:
                    try:
                        if getattr(entry, "updated_parsed", None):
                            updated_at_val = datetime.fromtimestamp(
                                time.mktime(entry.updated_parsed), tz=timezone.utc
                            ).isoformat()
                        elif getattr(entry, "updated", None):
                            parsed_up = date_parser.parse(entry.updated)
                            if parsed_up.tzinfo is None:
                                parsed_up = parsed_up.replace(tzinfo=timezone.utc)
                            updated_at_val = parsed_up.isoformat()
                    except Exception:
                        pass

                # 5. Author Extraction
                author_names: List[str] = []
                if hasattr(entry, "authors") and isinstance(entry.authors, list):
                    for a in entry.authors:
                        name = getattr(a, "name", None) or (a.get("name") if isinstance(a, dict) else str(a))
                        if name and name.strip():
                            author_names.append(normalize_whitespace(name))
                elif hasattr(entry, "author") and entry.author:
                    author_names.append(normalize_whitespace(entry.author))

                # Build display author string (max 255 chars)
                if author_names:
                    if len(author_names) > 3:
                        display_author = f"{author_names[0]}, {author_names[1]}, et al."
                    else:
                        display_author = ", ".join(author_names)
                    if len(display_author) > 250:
                        display_author = display_author[:247] + "..."
                else:
                    display_author = None

                # 6. Categories & Tags
                categories: List[str] = []
                primary_category: Optional[str] = None

                # Check arxiv:primary_category
                primary_cat_dict = getattr(entry, "arxiv_primary_category", None) or entry.get("arxiv_primary_category")
                if isinstance(primary_cat_dict, dict):
                    primary_category = primary_cat_dict.get("term")
                elif isinstance(primary_cat_dict, str):
                    primary_category = primary_cat_dict

                # Collect all categories/tags
                tags = getattr(entry, "tags", []) or []
                for tag in tags:
                    term = getattr(tag, "term", None) or (tag.get("term") if isinstance(tag, dict) else str(tag))
                    if term and term.strip() and term.strip() not in categories:
                        categories.append(term.strip())

                if not primary_category and categories:
                    primary_category = categories[0]
                elif primary_category and primary_category not in categories:
                    categories.insert(0, primary_category)

                # 7. Additional ArXiv Metadata (DOI, journal_ref, comment, PDF link)
                doi = getattr(entry, "arxiv_doi", None) or entry.get("arxiv_doi")
                journal_ref = getattr(entry, "arxiv_journal_ref", None) or entry.get("arxiv_journal_ref")
                comment = getattr(entry, "arxiv_comment", None) or entry.get("arxiv_comment")
                if comment:
                    comment = normalize_whitespace(comment)

                # Locate PDF URL from links
                pdf_url = default_pdf_url
                links = getattr(entry, "links", []) or []
                for link in links:
                    l_type = getattr(link, "type", None) or (link.get("type") if isinstance(link, dict) else "")
                    l_title = getattr(link, "title", None) or (link.get("title") if isinstance(link, dict) else "")
                    l_href = getattr(link, "href", None) or (link.get("href") if isinstance(link, dict) else "")
                    if l_href and (l_type == "application/pdf" or l_title == "pdf" or "/pdf/" in l_href):
                        clean_link = canonicalize_url(l_href)
                        if clean_link.startswith("http://"):
                            clean_link = "https://" + clean_link[7:]
                        pdf_url = clean_link
                        break

                # 8. Deterministic Content Hash (based on title and abstract)
                content_hash = generate_content_hash(title, abstract)

                # 9. Structured Metadata
                metadata_json: Dict[str, Any] = {
                    "arxiv_id": base_id,
                    "version": version,
                    "raw_id": raw_id,
                    "authors": author_names,
                    "primary_category": primary_category,
                    "categories": categories,
                    "pdf_url": pdf_url,
                    "doi": doi,
                    "journal_ref": journal_ref,
                    "comment": comment,
                    "updated_at": updated_at_val,
                }

                article = NormalizedArticle(
                    source_id=source.id or uuid4(),
                    canonical_url=canonical_url,
                    title=title,
                    summary=abstract,
                    raw_content=abstract,  # Research papers: abstract represents the primary normalized text
                    author=display_author,
                    published_at=published_at,
                    fetched_at=now_utc,
                    external_id=base_id,
                    language=source.language or "en",
                    content_hash=content_hash,
                    content_type=ContentType.RESEARCH_PAPER,
                    thumbnail_url=None,
                    categories=categories,
                    metadata_json=metadata_json,
                )
                articles.append(article)

            except Exception as item_exc:
                logger.warning(
                    f"Skipping malformed ArXiv entry in source '{source.name}': {str(item_exc)}",
                    exc_info=True,
                )
                continue

        return articles

    def build_query_url(self, source: Source, start: int = 0, max_results: int = 25) -> str:
        """Construct the official ArXiv API query URL from source configuration."""
        config = source.config if isinstance(source.config, dict) else {}
        api_base = config.get("api_url", self.DEFAULT_API_URL)

        search_query = config.get(
            "search_query",
            "cat:cs.AI OR cat:cs.LG OR cat:cs.CL OR cat:cs.CV OR cat:cs.NE OR cat:stat.ML",
        )
        sort_by = config.get("sort_by", "submittedDate")
        sort_order = config.get("sort_order", "descending")

        params = {
            "search_query": search_query,
            "start": start,
            "max_results": max_results,
            "sortBy": sort_by,
            "sortOrder": sort_order,
        }
        return f"{api_base}?{urlencode(params)}"

    async def fetch_and_parse(self, source: Source) -> List[NormalizedArticle]:
        """
        Fetch and parse ArXiv research papers for the given source.
        Supports configurable pagination and respectful rate-limiting delays.
        """
        config = source.config if isinstance(source.config, dict) else {}
        max_results_per_page = int(config.get("max_results_per_page", 25))
        max_pages = int(config.get("max_pages", 1))
        rate_limit_delay = float(config.get("rate_limit_delay_seconds", self.DEFAULT_RATE_LIMIT_DELAY))

        all_articles: List[NormalizedArticle] = []
        seen_external_ids = set()

        for page in range(max_pages):
            start = page * max_results_per_page
            query_url = self.build_query_url(source, start=start, max_results=max_results_per_page)

            logger.info(
                f"Fetching ArXiv papers for '{source.name}' (page={page + 1}/{max_pages}, start={start}, max={max_results_per_page})"
            )

            raw_xml = await self.http_client.fetch(query_url)
            page_articles = self.parse_feed_content(raw_xml, source)

            if not page_articles:
                logger.info(f"No ArXiv articles returned on page {page + 1}. Terminating pagination.")
                break

            new_articles = 0
            for art in page_articles:
                if art.external_id not in seen_external_ids:
                    seen_external_ids.add(art.external_id)
                    all_articles.append(art)
                    new_articles += 1

            logger.info(
                f"Page {page + 1} fetched: {len(page_articles)} entries ({new_articles} unique added, total={len(all_articles)})"
            )

            # If fewer articles returned than requested, we reached the end of results
            if len(page_articles) < max_results_per_page:
                break

            # If more pages to query, pause politely per ArXiv API guidelines
            if page + 1 < max_pages:
                logger.debug(f"ArXiv rate limit delay: pausing for {rate_limit_delay:.1f}s")
                await asyncio.sleep(rate_limit_delay)

        return all_articles
