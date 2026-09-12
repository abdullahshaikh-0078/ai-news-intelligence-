import uuid
import pytest
from app.domain.models.source import SourceType
from app.infrastructure.models.source import Source
from app.ingestion.adapters.hackernews_adapter import (
    HackerNewsAdapter,
    is_ai_relevant,
    strip_html,
)


def test_strip_html():
    """Verify HTML markup is converted to clean text."""
    raw = "<p>Ask HN: How are you running <b>local LLMs</b>?</p><br/>Check out this link."
    clean = strip_html(raw)
    assert clean == "Ask HN: How are you running local LLMs? Check out this link."
    assert strip_html("") == ""
    assert strip_html(None) == ""


def test_is_ai_relevant_keywords():
    """Verify deterministic AI keyword matching and word-boundary protection."""
    config = {
        "filter_ai_only": True,
        "min_score": 0,
        "include_keywords": ["ai", "llm", "transformer", "neural", "deep learning"],
        "exclude_keywords": ["crypto", "bitcoin"],
    }

    # True positives
    assert is_ai_relevant("Show HN: Fast LLM inference engine", "https://example.com/llm", 10, config)
    assert is_ai_relevant("State of AI in 2026", "https://example.com", 25, config)
    assert is_ai_relevant("Visualizing Transformer Attention Weights", None, 50, config)
    assert is_ai_relevant("Deep learning for robotics", None, 5, config)

    # Word-boundary protection: "paid", "chain", "domain" should NOT trigger "ai"
    assert not is_ai_relevant("How I got paid for consulting", "https://example.com/paid", 20, config)
    assert not is_ai_relevant("Supply chain bottlenecks in Europe", None, 30, config)
    assert not is_ai_relevant("Domain name registration changes", None, 15, config)

    # Exclude filter overrides include keywords
    assert not is_ai_relevant("AI agents trading crypto on Solana", None, 100, config)
    assert not is_ai_relevant("Bitcoin price prediction with LLM", None, 50, config)

    # Min score filter
    config_score = {**config, "min_score": 50}
    assert not is_ai_relevant("Show HN: Fast LLM engine", None, 10, config_score)
    assert is_ai_relevant("Show HN: Fast LLM engine", None, 60, config_score)

    # Filter disabled accepts all
    assert is_ai_relevant("Random unrelated gardening tips", None, 1, {"filter_ai_only": False})


def test_hackernews_adapter_external_story():
    """Verify normalization of external article submission."""
    adapter = HackerNewsAdapter()
    source = Source(
        id=uuid.uuid4(),
        name="Hacker News AI & Tech",
        slug="hacker-news",
        type=SourceType.WEB.value,
        url="https://news.ycombinator.com",
    )

    raw_item = {
        "id": 41500001,
        "type": "story",
        "by": "norvig",
        "time": 1725840000,
        "title": "OpenAI releases new reasoning model with formal proofs",
        "url": "https://openai.com/index/formal-proofs?utm_source=hn",
        "score": 342,
        "descendants": 128,
    }

    article = adapter.normalize_story(raw_item, source)
    assert article is not None
    assert article.external_id == "hn:41500001"
    assert article.canonical_url == "https://openai.com/index/formal-proofs"
    assert article.author == "norvig"
    assert article.title == "OpenAI releases new reasoning model with formal proofs"
    assert article.summary is None
    assert article.metadata_json["score"] == 342
    assert article.metadata_json["comments"] == 128
    assert article.metadata_json["domain"] == "openai.com"
    assert article.metadata_json["hn_url"] == "https://news.ycombinator.com/item?id=41500001"
    assert article.metadata_json["article_url"] == "https://openai.com/index/formal-proofs"


def test_hackernews_adapter_self_post():
    """Verify normalization of self-post (Ask HN / Show HN without external URL)."""
    adapter = HackerNewsAdapter()
    source = Source(
        id=uuid.uuid4(),
        name="Hacker News AI & Tech",
        slug="hacker-news",
        type=SourceType.WEB.value,
        url="https://news.ycombinator.com",
    )

    raw_item = {
        "id": 41500002,
        "type": "story",
        "by": "karpathy",
        "time": 1725845000,
        "title": "Ask HN: What is your favorite local LLM runtime in 2026?",
        "text": "<p>Looking for lightweight C++ or Rust inference runtimes with <b>Apple Silicon</b> support.</p>",
        "score": 195,
        "descendants": 84,
    }

    article = adapter.normalize_story(raw_item, source)
    assert article is not None
    assert article.external_id == "hn:41500002"
    assert article.canonical_url == "https://news.ycombinator.com/item?id=41500002"
    assert article.author == "karpathy"
    assert "lightweight C++ or Rust inference runtimes" in article.summary
    assert article.metadata_json["score"] == 195
    assert article.metadata_json["comments"] == 84
    assert article.metadata_json["domain"] == "news.ycombinator.com"
    assert article.metadata_json["article_url"] is None


def test_hackernews_adapter_discards_irrelevant_and_non_stories():
    """Verify non-story types, deleted items, dead items, and non-AI items are skipped."""
    adapter = HackerNewsAdapter()
    source = Source(
        id=uuid.uuid4(),
        name="Hacker News AI & Tech",
        slug="hacker-news",
        type=SourceType.WEB.value,
        url="https://news.ycombinator.com",
    )

    # Comment type
    comment_item = {"id": 1, "type": "comment", "text": "This AI model is great"}
    assert adapter.normalize_story(comment_item, source) is None

    # Job type
    job_item = {"id": 2, "type": "job", "title": "AI Engineer at Startup"}
    assert adapter.normalize_story(job_item, source) is None

    # Deleted story
    deleted_item = {"id": 3, "type": "story", "title": "New AI model", "deleted": True}
    assert adapter.normalize_story(deleted_item, source) is None

    # Dead story
    dead_item = {"id": 4, "type": "story", "title": "New AI model", "dead": True}
    assert adapter.normalize_story(dead_item, source) is None

    # Unrelated tech topic
    unrelated_item = {
        "id": 5,
        "type": "story",
        "title": "Why we rewrote our CSS in plain Vanilla CSS",
        "score": 50,
    }
    assert adapter.normalize_story(unrelated_item, source) is None
