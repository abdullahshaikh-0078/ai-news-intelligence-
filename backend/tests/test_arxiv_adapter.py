import uuid
import pytest
from app.domain.models.source import SourceType
from app.infrastructure.models.source import Source
from app.ingestion.adapters.arxiv_adapter import (
    ArXivAdapter,
    extract_arxiv_identity,
    normalize_whitespace,
)

SAMPLE_ARXIV_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <title type="html">ArXiv Query: cat:cs.AI</title>
  <id>http://arxiv.org/api/query</id>
  <updated>2026-09-07T00:00:00Z</updated>
  <entry>
    <id>http://arxiv.org/abs/2501.12345v2</id>
    <updated>2026-09-05T14:30:00Z</updated>
    <published>2026-09-01T12:00:00Z</published>
    <title>
      Scalable Multimodal Alignment
      Via Direct Preference Optimization
    </title>
    <summary>
      Recent breakthroughs in multimodal foundation models require
      robust alignment algorithms. We present a scalable direct preference
      optimization framework.
    </summary>
    <author>
      <name>Alice Smith</name>
    </author>
    <author>
      <name>Bob Jones</name>
    </author>
    <author>
      <name>Carol Vance</name>
    </author>
    <author>
      <name>David Miller</name>
    </author>
    <arxiv:doi>10.1000/182</arxiv:doi>
    <arxiv:journal_ref>Nature Machine Intelligence 2026</arxiv:journal_ref>
    <arxiv:comment>Accepted at NeurIPS 2026; 18 pages, 6 figures</arxiv:comment>
    <arxiv:primary_category term="cs.AI" scheme="http://arxiv.org/schemas/atom"/>
    <category term="cs.AI" scheme="http://arxiv.org/schemas/atom"/>
    <category term="cs.LG" scheme="http://arxiv.org/schemas/atom"/>
    <category term="stat.ML" scheme="http://arxiv.org/schemas/atom"/>
    <link href="http://arxiv.org/abs/2501.12345v2" rel="alternate" type="text/html"/>
    <link title="pdf" href="http://arxiv.org/pdf/2501.12345v2" rel="related" type="application/pdf"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/cs/0112017v1</id>
    <published>2001-12-15T09:00:00Z</published>
    <title>Classical Search Heuristics</title>
    <summary>A foundational study on graph search heuristics.</summary>
    <author>
      <name>Grace Hopper</name>
    </author>
    <category term="cs.AI" scheme="http://arxiv.org/schemas/atom"/>
    <link href="http://arxiv.org/abs/cs/0112017v1" rel="alternate" type="text/html"/>
  </entry>
</feed>
"""


def test_extract_arxiv_identity_modern():
    """Verify identity extraction for modern ArXiv ID formats."""
    # Versioned modern ID
    base_id, version, canon, pdf = extract_arxiv_identity("http://arxiv.org/abs/2501.12345v2")
    assert base_id == "2501.12345"
    assert version == "v2"
    assert canon == "https://arxiv.org/abs/2501.12345"
    assert pdf == "https://arxiv.org/pdf/2501.12345.pdf"

    # Unversioned modern ID
    base_id2, version2, canon2, pdf2 = extract_arxiv_identity("https://arxiv.org/abs/2303.08774")
    assert base_id2 == "2303.08774"
    assert version2 is None
    assert canon2 == "https://arxiv.org/abs/2303.08774"
    assert pdf2 == "https://arxiv.org/pdf/2303.08774.pdf"

    # Direct identifier string
    base_id3, version3, canon3, _ = extract_arxiv_identity("2406.01234v1")
    assert base_id3 == "2406.01234"
    assert version3 == "v1"
    assert canon3 == "https://arxiv.org/abs/2406.01234"


def test_extract_arxiv_identity_legacy():
    """Verify identity extraction for legacy pre-2007 ArXiv ID formats."""
    base_id, version, canon, pdf = extract_arxiv_identity("http://arxiv.org/abs/cs/0112017v1")
    assert base_id == "cs/0112017"
    assert version == "v1"
    assert canon == "https://arxiv.org/abs/cs/0112017"
    assert pdf == "https://arxiv.org/pdf/cs/0112017.pdf"

    # Math archive legacy
    base_id2, version2, canon2, _ = extract_arxiv_identity("https://arxiv.org/abs/math.GT/0309136v3")
    assert base_id2 == "math.GT/0309136"
    assert version2 == "v3"
    assert canon2 == "https://arxiv.org/abs/math.GT/0309136"


def test_normalize_whitespace():
    """Verify multiline newlines and multiple spaces are condensed cleanly."""
    raw = "\n   Deep   Learning \n\n  for Code Generation\t  \n"
    assert normalize_whitespace(raw) == "Deep Learning for Code Generation"
    assert normalize_whitespace(None) == ""


def test_arxiv_adapter_parse_feed_content():
    """Verify parsing of complete ArXiv Atom XML feed entries."""
    adapter = ArXivAdapter()
    source = Source(
        id=uuid.uuid4(),
        name="ArXiv AI Research",
        slug="arxiv-ai",
        type=SourceType.ARXIV.value,
        url="https://export.arxiv.org/api/query",
        language="en",
    )

    articles = adapter.parse_feed_content(SAMPLE_ARXIV_ATOM, source)
    assert len(articles) == 2

    # Entry 1: Modern paper with rich metadata
    art1 = articles[0]
    assert art1.external_id == "2501.12345"
    assert art1.canonical_url == "https://arxiv.org/abs/2501.12345"
    assert art1.title == "Scalable Multimodal Alignment Via Direct Preference Optimization"
    assert "multimodal foundation models" in art1.summary
    assert art1.author == "Alice Smith, Bob Jones, et al."
    assert art1.metadata_json["authors"] == ["Alice Smith", "Bob Jones", "Carol Vance", "David Miller"]
    assert art1.metadata_json["primary_category"] == "cs.AI"
    assert "cs.LG" in art1.categories
    assert "stat.ML" in art1.categories
    assert art1.metadata_json["doi"] == "10.1000/182"
    assert art1.metadata_json["journal_ref"] == "Nature Machine Intelligence 2026"
    assert "NeurIPS 2026" in art1.metadata_json["comment"]
    assert "https://arxiv.org/pdf/2501.12345v2" in art1.metadata_json["pdf_url"]
    assert art1.metadata_json["version"] == "v2"

    # Entry 2: Legacy format paper
    art2 = articles[1]
    assert art2.external_id == "cs/0112017"
    assert art2.canonical_url == "https://arxiv.org/abs/cs/0112017"
    assert art2.title == "Classical Search Heuristics"
    assert art2.author == "Grace Hopper"
    assert art2.metadata_json["authors"] == ["Grace Hopper"]
    assert "cs.AI" in art2.categories


def test_arxiv_adapter_build_query_url():
    """Verify construction of query URL with custom pagination parameters."""
    adapter = ArXivAdapter()
    source = Source(
        id=uuid.uuid4(),
        name="ArXiv Vision",
        slug="arxiv-cv",
        type=SourceType.ARXIV.value,
        url="https://export.arxiv.org/api/query",
        config={
            "search_query": "cat:cs.CV",
            "sort_by": "lastUpdatedDate",
            "sort_order": "ascending",
        },
    )

    url = adapter.build_query_url(source, start=50, max_results=25)
    assert "https://export.arxiv.org/api/query?" in url
    assert "search_query=cat%3Acs.CV" in url
    assert "start=50" in url
    assert "max_results=25" in url
    assert "sortBy=lastUpdatedDate" in url
    assert "sortOrder=ascending" in url


def test_arxiv_adapter_malformed_xml():
    """Verify that malformed or empty XML returns an empty list without raising exceptions."""
    adapter = ArXivAdapter()
    source = Source(
        id=uuid.uuid4(),
        name="ArXiv Corrupt",
        slug="arxiv-corrupt",
        type=SourceType.ARXIV.value,
        url="https://export.arxiv.org/api/query",
    )

    assert adapter.parse_feed_content("<broken>xml", source) == []
    assert adapter.parse_feed_content("", source) == []
