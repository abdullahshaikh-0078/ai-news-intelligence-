import uuid
from app.infrastructure.models.source import Source
from app.ingestion.adapters.rss_adapter import RSSAdapter

SAMPLE_RSS_2 = """<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
<channel>
    <title>AI Lab Feed</title>
    <link>https://ailab.org</link>
    <description>Latest AI Research</description>
    <item>
        <title>New Foundation Model Breakthrough</title>
        <link>https://ailab.org/news/foundation-model?utm_source=feed</link>
        <description>Summary of the research breakthrough</description>
        <author>Dr. Jane Doe</author>
        <pubDate>Mon, 07 Sep 2026 10:00:00 +0000</pubDate>
        <guid>guid-12345</guid>
        <category>Machine Learning</category>
        <category>NLP</category>
    </item>
    <item>
        <title>Missing Fields Article</title>
        <link>https://ailab.org/news/missing-fields</link>
    </item>
    <item>
        <!-- Malformed entry: missing title -->
        <link>https://ailab.org/news/no-title</link>
    </item>
</channel>
</rss>
"""

SAMPLE_ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
    <title>Atom AI Lab</title>
    <link href="https://atom-ai.org"/>
    <updated>2026-09-07T10:00:00Z</updated>
    <entry>
        <title>Reasoning Models at Scale</title>
        <link rel="alternate" type="text/html" href="https://atom-ai.org/posts/reasoning-scale#comments"/>
        <id>urn:uuid:1225c695-cfb8-4ebb-aaaa-80da344efa6a</id>
        <published>2026-09-07T08:30:00Z</published>
        <summary>A detailed look into test-time compute scaling.</summary>
        <content type="html">&lt;p&gt;Full HTML content of the paper&lt;/p&gt;</content>
        <author>
            <name>Alex Rivera</name>
        </author>
        <category term="Reasoning"/>
    </entry>
</feed>
"""


def test_parse_rss_2_feed():
    """Verify parsing standard RSS 2.0 XML with full metadata and malformed entry isolation."""
    source = Source(
        id=uuid.uuid4(),
        name="AI Lab",
        slug="ai-lab",
        type="RSS",
        url="https://ailab.org/feed.xml",
    )
    adapter = RSSAdapter()
    articles = adapter.parse_feed_content(SAMPLE_RSS_2, source)

    assert len(articles) == 2  # The 3rd entry missing title is gracefully skipped

    # Verify first entry
    first = articles[0]
    assert first.title == "New Foundation Model Breakthrough"
    assert first.canonical_url == "https://ailab.org/news/foundation-model"
    assert first.summary == "Summary of the research breakthrough"
    assert first.external_id == "guid-12345"
    assert "Machine Learning" in first.categories
    assert "NLP" in first.categories
    assert first.published_at.year == 2026
    assert first.content_hash is not None

    # Verify second entry with missing fields
    second = articles[1]
    assert second.title == "Missing Fields Article"
    assert second.canonical_url == "https://ailab.org/news/missing-fields"
    assert second.summary is None
    assert second.author is None
    assert second.published_at is not None  # Fallback to now


def test_parse_atom_feed():
    """Verify parsing Atom XML feed with link[rel=alternate], content, and UUID id."""
    source = Source(
        id=uuid.uuid4(),
        name="Atom Lab",
        slug="atom-lab",
        type="RSS",
        url="https://atom-ai.org/atom.xml",
    )
    adapter = RSSAdapter()
    articles = adapter.parse_feed_content(SAMPLE_ATOM, source)

    assert len(articles) == 1
    item = articles[0]
    assert item.title == "Reasoning Models at Scale"
    assert item.canonical_url == "https://atom-ai.org/posts/reasoning-scale"
    assert item.summary == "A detailed look into test-time compute scaling."
    assert item.author == "Alex Rivera"
    assert item.external_id == "urn:uuid:1225c695-cfb8-4ebb-aaaa-80da344efa6a"
    assert "Reasoning" in item.categories
    assert item.published_at.year == 2026


def test_parse_invalid_xml():
    """Verify corrupted or non-XML text returns empty list without raising exceptions."""
    source = Source(
        id=uuid.uuid4(),
        name="Broken Feed",
        slug="broken-feed",
        type="RSS",
        url="https://broken.org/rss",
    )
    adapter = RSSAdapter()
    articles = adapter.parse_feed_content("This is not valid XML at all <!@#$%", source)
    assert articles == []
