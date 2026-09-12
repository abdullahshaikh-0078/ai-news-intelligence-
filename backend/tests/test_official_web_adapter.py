import pytest
import uuid
from app.infrastructure.models.source import Source
from app.ingestion.adapters.web_adapter import AnthropicNewsParser, GenericHTMLCardParser, OfficialWebAdapter

SAMPLE_ANTHROPIC_HTML = """<!DOCTYPE html>
<html>
<body>
<div class="news-feed">
    <a href="/news/claude-opus-5">
        <span class="badge">Product</span>
        <span class="date">Jul 24, 2026</span>
        <h3>Introducing Claude Opus 5</h3>
        <p>Opus 5 represents a step change in reasoning and multimodal capabilities.</p>
    </a>
    <a href="/news/frontier-safeguards">
        <span class="badge">Safety</span>
        <span class="date">Sep 01, 2026</span>
        <h3>Enterprise Frontier Safeguards</h3>
        <p>New safety standards for enterprise deployments.</p>
    </a>
    <a href="/news/no-date-article">
        <h3>Article Without Explicit Date</h3>
        <p>Valid article with fallback timestamp.</p>
    </a>
    <a href="/other-page">
        <h3>Ignore Non-News Link</h3>
    </a>
</div>
</body>
</html>
"""

SAMPLE_GENERIC_HTML = """<!DOCTYPE html>
<html>
<body>
<div class="container">
    <article class="post-card">
        <h2><a href="/posts/ai-system-design">AI System Architecture</a></h2>
        <time datetime="2026-08-15T12:00:00Z">August 15, 2026</time>
        <p>Deep dive into modular agentic systems.</p>
    </article>
</div>
</body>
</html>
"""


def test_anthropic_news_parser():
    """Verify parsing Anthropic HTML cards into NormalizedArticle instances."""
    source = Source(
        id=uuid.uuid4(),
        name="Anthropic News & Research",
        slug="anthropic-news",
        type="WEB",
        url="https://www.anthropic.com/news",
    )
    articles = AnthropicNewsParser.parse(SAMPLE_ANTHROPIC_HTML, source)

    assert len(articles) == 3  # Non-news link ignored

    # Verify first article (Opus 5)
    first = articles[0]
    assert first.title == "Introducing Claude Opus 5"
    assert first.canonical_url == "https://www.anthropic.com/news/claude-opus-5"
    assert first.summary == "Opus 5 represents a step change in reasoning and multimodal capabilities."
    assert "Product" in first.categories
    assert first.published_at.year == 2026
    assert first.published_at.month == 7
    assert first.published_at.day == 24
    assert first.author == "Anthropic"
    assert first.content_hash is not None

    # Verify second article (Safety)
    second = articles[1]
    assert second.title == "Enterprise Frontier Safeguards"
    assert "Safety" in second.categories
    assert second.published_at.month == 9

    # Verify article with no explicit date has safe UTC fallback
    third = articles[2]
    assert third.title == "Article Without Explicit Date"
    assert third.published_at is not None


def test_generic_html_card_parser():
    """Verify generic HTML article card fallback parser."""
    source = Source(
        id=uuid.uuid4(),
        name="AI Lab Generic",
        slug="ai-lab-generic",
        type="WEB",
        url="https://genericlab.ai/posts",
    )
    articles = GenericHTMLCardParser.parse(SAMPLE_GENERIC_HTML, source)

    assert len(articles) == 1
    art = articles[0]
    assert art.title == "AI System Architecture"
    assert art.canonical_url == "https://genericlab.ai/posts/ai-system-design"
    assert art.summary == "Deep dive into modular agentic systems."
    assert art.published_at.year == 2026
    assert art.published_at.month == 8


@pytest.mark.asyncio
async def test_official_web_adapter_feed_url_priority(monkeypatch):
    """Verify OfficialWebAdapter uses official structured feed_url when configured."""
    source = Source(
        id=uuid.uuid4(),
        name="OpenAI News",
        slug="openai-news",
        type="WEB",
        url="https://openai.com/news",
        config={"feed_url": "https://openai.com/news/rss.xml", "organization": "OpenAI"},
    )

    feed_xml = """<?xml version="1.0" encoding="UTF-8" ?>
    <rss version="2.0">
    <channel>
        <title>OpenAI News</title>
        <link>https://openai.com</link>
        <item>
            <title>GPT-5 Announcement</title>
            <link>https://openai.com/index/gpt-5</link>
            <description>Next-generation frontier model</description>
            <pubDate>Mon, 07 Sep 2026 12:00:00 GMT</pubDate>
        </item>
    </channel>
    </rss>
    """

    async def mock_fetch(self, url):
        assert url == "https://openai.com/news/rss.xml"
        return feed_xml

    monkeypatch.setattr("app.ingestion.http_client.FeedHttpClient.fetch", mock_fetch)

    adapter = OfficialWebAdapter()
    articles = await adapter.fetch_and_parse(source)

    assert len(articles) == 1
    assert articles[0].title == "GPT-5 Announcement"
    assert articles[0].canonical_url == "https://openai.com/index/gpt-5"
