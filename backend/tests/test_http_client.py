import pytest
import httpx
from app.ingestion.http_client import FeedFetchError, FeedHttpClient


@pytest.mark.asyncio
async def test_http_client_successful_fetch(monkeypatch):
    """Verify standard successful GET request."""
    async def mock_get(self, url, headers=None):
        return httpx.Response(200, text="<xml>feed</xml>", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    client = FeedHttpClient()
    text = await client.fetch("https://example.com/feed.xml")
    assert text == "<xml>feed</xml>"


@pytest.mark.asyncio
async def test_http_client_retry_on_500(monkeypatch):
    """Verify retry with backoff succeeds if transient 500 error is resolved."""
    attempts = 0

    async def mock_get(self, url, headers=None):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            req = httpx.Request("GET", url)
            return httpx.Response(500, request=req)
        return httpx.Response(200, text="<rss>recovered</rss>", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    client = FeedHttpClient(max_retries=2, backoff_factor=0.01)
    text = await client.fetch("https://example.com/transient.xml")
    assert text == "<rss>recovered</rss>"
    assert attempts == 2


@pytest.mark.asyncio
async def test_http_client_non_retryable_404(monkeypatch):
    """Verify 404 immediately raises FeedFetchError without exhausting retries."""
    attempts = 0

    async def mock_get(self, url, headers=None):
        nonlocal attempts
        attempts += 1
        req = httpx.Request("GET", url)
        return httpx.Response(404, request=req)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    client = FeedHttpClient(max_retries=3)
    with pytest.raises(FeedFetchError) as exc_info:
        await client.fetch("https://example.com/notfound.xml")
    assert "404" in str(exc_info.value)
    assert attempts == 1


@pytest.mark.asyncio
async def test_http_client_response_size_protection(monkeypatch):
    """Verify exceeding max_bytes raises FeedFetchError."""
    async def mock_get(self, url, headers=None):
        large_body = b"A" * 2048
        return httpx.Response(200, content=large_body, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    client = FeedHttpClient(max_bytes=1000)  # 1000 byte limit
    with pytest.raises(FeedFetchError) as exc_info:
        await client.fetch("https://example.com/large.xml")
    assert "exceeds maximum permitted" in str(exc_info.value)
