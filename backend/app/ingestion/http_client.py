import asyncio
import json
from typing import Any, Dict, Optional
import httpx
from app.core.config import settings
from app.core.exceptions import AppException
from app.core.logging import logger


import re


def _sanitize_url(url: str) -> str:
    """Strip sensitive query parameters like API keys from URLs before logging."""
    return re.sub(r"([?&](?:key|api_key|token|secret)=)[^&]+", r"\1***", str(url), flags=re.IGNORECASE)


class FeedFetchError(AppException):
    """Raised when an external feed cannot be fetched via HTTP."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message=message, code="FEED_FETCH_ERROR", status_code=status_code)


class FeedHttpClient:
    """Robust async HTTP client with retry, backoff, timeout, and response-size protection."""

    def __init__(
        self,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
        backoff_factor: Optional[float] = None,
        user_agent: Optional[str] = None,
        max_bytes: Optional[int] = None,
    ):
        self.timeout = timeout or settings.INGESTION_HTTP_TIMEOUT
        self.max_retries = max_retries or settings.INGESTION_MAX_RETRIES
        self.backoff_factor = backoff_factor or settings.INGESTION_BACKOFF_FACTOR
        self.user_agent = user_agent or settings.INGESTION_USER_AGENT
        self.max_bytes = max_bytes or settings.INGESTION_MAX_RESPONSE_BYTES

    async def fetch(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Fetch content from remote URL as UTF-8 string with exponential backoff retries."""
        req_headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json, application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        }
        if headers:
            req_headers.update(headers)

        timeout_config = httpx.Timeout(self.timeout, connect=self.timeout)
        safe_url = _sanitize_url(url)

        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                kwargs: Dict[str, Any] = {"headers": req_headers}
                if params is not None:
                    kwargs["params"] = params

                async with httpx.AsyncClient(timeout=timeout_config, follow_redirects=True) as client:
                    response = await client.get(url, **kwargs)

                    # Check response size from headers if available
                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > self.max_bytes:
                        raise FeedFetchError(
                            f"Response size {content_length} bytes exceeds maximum permitted ({self.max_bytes} bytes)"
                        )

                    response.raise_for_status()

                    content_bytes = response.content
                    if len(content_bytes) > self.max_bytes:
                        raise FeedFetchError(
                            f"Downloaded content size {len(content_bytes)} bytes exceeds maximum permitted ({self.max_bytes} bytes)"
                        )

                    return response.text

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                # Only retry on transient 5xx server errors
                if 500 <= status < 600 and attempt < self.max_retries:
                    sleep_time = self.backoff_factor * (2 ** (attempt - 1))
                    logger.warning(
                        f"HTTP {status} fetching {safe_url} (attempt {attempt}/{self.max_retries}). Retrying in {sleep_time:.2f}s..."
                    )
                    await asyncio.sleep(sleep_time)
                    last_error = exc
                    continue
                logger.error(f"HTTP {status} error fetching feed at {safe_url}: {str(exc)}")
                raise FeedFetchError(f"HTTP {status} error fetching feed: {str(exc)}") from exc

            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    sleep_time = self.backoff_factor * (2 ** (attempt - 1))
                    logger.warning(
                        f"Network error '{type(exc).__name__}' fetching {safe_url} (attempt {attempt}/{self.max_retries}). Retrying in {sleep_time:.2f}s..."
                    )
                    await asyncio.sleep(sleep_time)
                    continue
                logger.error(f"Network failure after {self.max_retries} attempts fetching {safe_url}: {str(exc)}")
                raise FeedFetchError(f"Failed to fetch feed after {self.max_retries} attempts: {str(exc)}") from exc

            except FeedFetchError:
                raise

            except Exception as exc:
                logger.error(f"Unexpected error fetching {safe_url}: {str(exc)}")
                raise FeedFetchError(f"Unexpected error fetching feed: {str(exc)}") from exc

        raise FeedFetchError(f"Failed to fetch {safe_url}: {str(last_error)}")

    async def fetch_json(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Fetch remote resource and decode as JSON."""
        text = await self.fetch(url, headers=headers, params=params)
        safe_url = _sanitize_url(url)
        try:
            return json.loads(text)
        except Exception as exc:
            logger.error(f"Failed to decode JSON from {safe_url}: {str(exc)}")
            raise FeedFetchError(f"Invalid JSON payload returned from {safe_url}: {str(exc)}") from exc
