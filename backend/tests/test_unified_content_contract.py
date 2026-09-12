from datetime import datetime, timezone
import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import AsyncMock

from app.domain.models.content_item import ContentType
from app.domain.models.source import SourceType
from app.domain.schemas.ingestion import NormalizedArticle
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.models.source import Source
from app.infrastructure.repositories.content_repo import ContentRepository
from app.infrastructure.repositories.source_repo import SourceRepository
from app.ingestion.adapters.arxiv_adapter import ArXivAdapter
from app.ingestion.adapters.hackernews_adapter import HackerNewsAdapter
from app.ingestion.adapters.rss_adapter import RSSAdapter
from app.ingestion.adapters.web_adapter import OfficialWebAdapter
from app.ingestion.adapters.youtube_adapter import YouTubeAdapter
from app.ingestion.canonicalizer import generate_content_hash
from app.ingestion.orchestrator import IngestionOrchestrator


def test_canonical_content_type_enum():
    """Verify ContentType enum supports all canonical types with exact string values."""
    assert ContentType.ARTICLE.value == "ARTICLE"
    assert ContentType.RESEARCH_PAPER.value == "RESEARCH_PAPER"
    assert ContentType.VIDEO.value == "VIDEO"
    assert ContentType.COMMUNITY_POST.value == "COMMUNITY_POST"


def test_cross_source_canonical_contract_conformance():
    """Verify that representative items from all 5 ingestion families satisfy the canonical contract."""
    dummy_source_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # 1. RSS Article
    rss_item = NormalizedArticle(
        source_id=dummy_source_id,
        canonical_url="https://www.technologyreview.com/2026/09/08/ai-breakthrough",
        title="MIT Tech Review AI Article",
        summary="A summary of the breakthrough",
        raw_content="Full article content",
        author="Tech Review Staff",
        published_at=now,
        fetched_at=now,
        external_id="https://www.technologyreview.com/2026/09/08/ai-breakthrough",
        language="en",
        content_hash=generate_content_hash("MIT Tech Review AI Article", "Full article content", "Tech Review Staff"),
        content_type=ContentType.ARTICLE,
        thumbnail_url="https://img.technologyreview.com/lead.jpg",
        categories=["AI", "Robotics"],
        metadata_json={"feed_version": "rss20", "comments_url": "https://example.com/comments"},
    )

    # 2. Official Lab Announcement
    lab_item = NormalizedArticle(
        source_id=dummy_source_id,
        canonical_url="https://www.anthropic.com/news/claude-3-7",
        title="Claude 3.7 Release Announcement",
        summary="Anthropic announces Claude 3.7",
        raw_content="Full post content",
        author="Anthropic",
        published_at=now,
        fetched_at=now,
        external_id="/news/claude-3-7",
        language="en",
        content_hash=generate_content_hash("Claude 3.7 Release Announcement", "Full post content", "Anthropic"),
        content_type=ContentType.ARTICLE,
        thumbnail_url="https://www.anthropic.com/images/card.png",
        categories=["Announcements"],
        metadata_json={"organization": "Anthropic", "parser": "anthropic_html"},
    )

    # 3. ArXiv Research Paper
    arxiv_item = NormalizedArticle(
        source_id=dummy_source_id,
        canonical_url="https://arxiv.org/abs/2601.12345",
        title="Scaling Laws for Hybrid Reasoning Models",
        summary="Abstract of the research paper",
        raw_content="Abstract of the research paper",
        author="Dr. Jane Doe, Dr. John Smith, et al.",
        published_at=now,
        fetched_at=now,
        external_id="2601.12345",
        language="en",
        content_hash=generate_content_hash("Scaling Laws for Hybrid Reasoning Models", "Abstract", "Dr. Jane Doe"),
        content_type=ContentType.RESEARCH_PAPER,
        thumbnail_url=None,
        categories=["cs.AI", "cs.LG", "stat.ML"],
        metadata_json={"arxiv_id": "2601.12345", "pdf_url": "https://arxiv.org/pdf/2601.12345.pdf", "version": "v1"},
    )

    # 4. Hacker News Community Story
    hn_item = NormalizedArticle(
        source_id=dummy_source_id,
        canonical_url="https://news.ycombinator.com/item?id=49999999",
        title="Ask HN: What is your preferred local LLM stack?",
        summary="Discussion on local models",
        raw_content="Discussion on local models",
        author="ai_hacker",
        published_at=now,
        fetched_at=now,
        external_id="hn:49999999",
        language="en",
        content_hash=generate_content_hash("Ask HN: What is your preferred local LLM stack?", "Discussion", "ai_hacker"),
        content_type=ContentType.COMMUNITY_POST,
        thumbnail_url=None,
        categories=["Community", "Developer", "Hacker News"],
        metadata_json={"hn_item_id": 49999999, "score": 142, "comments": 88, "domain": "news.ycombinator.com"},
    )

    # 5. YouTube Video
    yt_item = NormalizedArticle(
        source_id=dummy_source_id,
        canonical_url="https://www.youtube.com/watch?v=vid_canonical_1",
        title="DeepSeek R1 Architecture Explained!",
        summary="Video breakdown",
        raw_content="Full video description",
        author="Two Minute Papers",
        published_at=now,
        fetched_at=now,
        external_id="youtube:vid_canonical_1",
        language="en",
        content_hash=generate_content_hash("DeepSeek R1 Architecture Explained!", "Description", "Two Minute Papers"),
        content_type=ContentType.VIDEO,
        thumbnail_url="https://i.ytimg.com/vi/vid_canonical_1/maxresdefault.jpg",
        categories=["AI", "category:28"],
        metadata_json={"video_id": "vid_canonical_1", "duration": "PT12M30S", "view_count": 89000, "like_count": 7200},
    )

    canonical_items = [rss_item, lab_item, arxiv_item, hn_item, yt_item]

    for item in canonical_items:
        # Check mandatory canonical contract fields
        assert isinstance(item.source_id, uuid.UUID)
        assert item.canonical_url.startswith("http")
        assert len(item.title) > 0
        assert isinstance(item.published_at, datetime)
        assert item.published_at.tzinfo is not None
        assert isinstance(item.fetched_at, datetime)
        assert item.fetched_at.tzinfo is not None
        assert len(item.content_hash) == 64
        assert isinstance(item.content_type, ContentType)
        assert isinstance(item.categories, list)
        assert isinstance(item.metadata_json, dict)

    assert rss_item.content_type == ContentType.ARTICLE
    assert lab_item.content_type == ContentType.ARTICLE
    assert arxiv_item.content_type == ContentType.RESEARCH_PAPER
    assert hn_item.content_type == ContentType.COMMUNITY_POST
    assert yt_item.content_type == ContentType.VIDEO


@pytest.mark.asyncio
async def test_canonical_persistence_and_query_by_content_type(db_session: AsyncSession):
    """Verify that ContentItem persists content_type and thumbnail_url, and supports querying by content_type."""
    source_repo = SourceRepository(db_session)
    content_repo = ContentRepository(db_session)
    orchestrator = IngestionOrchestrator(db_session)

    src = await source_repo.add(
        Source(
            name="Contract Test Source",
            slug="contract-test-src",
            type=SourceType.WEB.value,
            url="https://contract-test.example.com",
            enabled=True,
        )
    )

    now = datetime.now(timezone.utc)
    items = [
        NormalizedArticle(
            source_id=src.id,
            canonical_url=f"https://contract-test.example.com/item-{ctype.value}",
            title=f"Sample {ctype.value}",
            published_at=now,
            fetched_at=now,
            external_id=f"ext:{ctype.value}",
            content_hash=generate_content_hash(f"Sample {ctype.value}", "", "Author"),
            content_type=ctype,
            thumbnail_url=f"https://img.example.com/{ctype.value.lower()}.png" if ctype in (ContentType.ARTICLE, ContentType.VIDEO) else None,
            metadata_json={"custom_attr": ctype.value},
        )
        for ctype in (ContentType.ARTICLE, ContentType.RESEARCH_PAPER, ContentType.VIDEO, ContentType.COMMUNITY_POST)
    ]

    mock_adapter = AsyncMock()
    mock_adapter.fetch_and_parse.return_value = items
    orchestrator.adapters[src.type] = mock_adapter

    res = await orchestrator.ingest_source(src)
    assert res.entries_inserted == 4

    # Verify querying by content_type
    articles = await content_repo.list_by_content_type(ContentType.ARTICLE.value)
    assert any(a.title == "Sample ARTICLE" for a in articles)
    article_row = next(a for a in articles if a.title == "Sample ARTICLE")
    assert article_row.content_type == "ARTICLE"
    assert article_row.thumbnail_url == "https://img.example.com/article.png"

    papers = await content_repo.list_by_content_type(ContentType.RESEARCH_PAPER.value)
    assert any(p.title == "Sample RESEARCH_PAPER" for p in papers)
    paper_row = next(p for p in papers if p.title == "Sample RESEARCH_PAPER")
    assert paper_row.content_type == "RESEARCH_PAPER"
    assert paper_row.thumbnail_url is None

    videos = await content_repo.list_by_content_type(ContentType.VIDEO.value)
    assert any(v.title == "Sample VIDEO" for v in videos)
    video_row = next(v for v in videos if v.title == "Sample VIDEO")
    assert video_row.content_type == "VIDEO"
    assert video_row.thumbnail_url == "https://img.example.com/video.png"

    posts = await content_repo.list_by_content_type(ContentType.COMMUNITY_POST.value)
    assert any(p.title == "Sample COMMUNITY_POST" for p in posts)
    post_row = next(p for p in posts if p.title == "Sample COMMUNITY_POST")
    assert post_row.content_type == "COMMUNITY_POST"
    assert post_row.thumbnail_url is None


def test_adapters_canonical_content_type_assignments():
    """Verify that each adapter class assigns the appropriate canonical ContentType."""
    # 1. RSSAdapter
    rss_adapter = RSSAdapter()
    sample_rss = """<?xml version="1.0"?>
    <rss version="2.0">
      <channel>
        <title>AI Feed</title>
        <item>
          <title>AI Article</title>
          <link>https://example.com/article-1</link>
          <description>Summary</description>
        </item>
      </channel>
    </rss>"""
    src = Source(name="Test RSS", type=SourceType.RSS.value, url="https://example.com/feed.xml")
    rss_articles = rss_adapter.parse_feed_content(sample_rss, src)
    assert len(rss_articles) == 1
    assert rss_articles[0].content_type == ContentType.ARTICLE

    # 2. ArXivAdapter
    arxiv_adapter = ArXivAdapter()
    sample_atom = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>http://arxiv.org/abs/2501.00001v1</id>
        <title>New AI Paper</title>
        <summary>Paper abstract</summary>
        <published>2026-01-01T00:00:00Z</published>
        <author><name>Alice</name></author>
      </entry>
    </feed>"""
    src_arxiv = Source(name="ArXiv", type=SourceType.ARXIV.value, url="https://export.arxiv.org/api/query")
    arxiv_articles = arxiv_adapter.parse_feed_content(sample_atom, src_arxiv)
    assert len(arxiv_articles) == 1
    assert arxiv_articles[0].content_type == ContentType.RESEARCH_PAPER

    # 3. HackerNewsAdapter
    hn_adapter = HackerNewsAdapter()
    hn_item_raw = {
        "id": 9999999,
        "type": "story",
        "title": "OpenAI releases new reasoning model",
        "url": "https://example.com/openai-model",
        "by": "sam",
        "time": 1725840000,
        "score": 100,
        "descendants": 20,
    }
    src_hn = Source(name="Hacker News", type=SourceType.WEB.value, url="https://news.ycombinator.com")
    hn_norm = hn_adapter.normalize_story(hn_item_raw, src_hn)
    assert hn_norm is not None
    assert hn_norm.content_type == ContentType.COMMUNITY_POST
