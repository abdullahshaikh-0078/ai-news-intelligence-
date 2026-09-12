from app.ingestion.canonicalizer import canonicalize_url, generate_content_hash


def test_canonicalize_url_basic():
    """Verify standard URL normalization."""
    url = "https://example.com/ai/article-1"
    assert canonicalize_url(url) == "https://example.com/ai/article-1"


def test_canonicalize_url_strips_tracking_and_utm():
    """Verify tracking and UTM parameters are stripped while preserving legitimate parameters."""
    raw = "https://example.com/news?utm_source=twitter&utm_medium=social&category=deep-learning&utm_campaign=launch&fbclid=12345"
    expected = "https://example.com/news?category=deep-learning"
    assert canonicalize_url(raw) == expected


def test_canonicalize_url_strips_fragments():
    """Verify URL fragments are removed."""
    raw = "https://example.com/post#section-2"
    assert canonicalize_url(raw) == "https://example.com/post"


def test_canonicalize_url_normalizes_host_and_port():
    """Verify lowercase host and stripping of default HTTP/HTTPS ports."""
    raw = "HTTPS://WWW.Example.COM:443/research"
    expected = "https://www.example.com/research"
    assert canonicalize_url(raw) == expected

    http_raw = "HTTP://Example.COM:80/feed"
    expected_http = "http://example.com/feed"
    assert canonicalize_url(http_raw) == expected_http


def test_canonicalize_url_trailing_slash():
    """Verify trailing slash is removed for non-root paths and preserved for root."""
    assert canonicalize_url("https://example.com/path/") == "https://example.com/path"
    assert canonicalize_url("https://example.com/") == "https://example.com/"


def test_deterministic_content_hash():
    """Verify SHA-256 content hashing is deterministic and case-insensitive on title."""
    h1 = generate_content_hash("https://example.com/a1", "New Transformer Model", "Full article content...")
    h2 = generate_content_hash("https://example.com/a1", "new transformer model", "Full article content...")
    assert h1 == h2

    # Different content produces different hash
    h3 = generate_content_hash("https://example.com/a1", "New Transformer Model", "Different content...")
    assert h1 != h3
