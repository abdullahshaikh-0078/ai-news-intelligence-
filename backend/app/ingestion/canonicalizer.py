import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Query parameters commonly injected by newsletters, RSS readers, ads, and trackers
TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "msclkid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
    "sr_source",
    "_hsenc",
    "_hsmi",
}


def canonicalize_url(url: str) -> str:
    """Deterministic URL normalization for deduplication and canonical identity."""
    if not url:
        return ""

    url = url.strip()
    parsed = urlparse(url)

    # Force scheme to lowercase (default to https if missing)
    scheme = (parsed.scheme or "https").lower()

    # Lowercase hostname and strip default ports (:80 for http, :443 for https)
    netloc = (parsed.netloc or "").lower()
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]

    # Normalize path: remove redundant consecutive slashes
    path = re.sub(r"/+", "/", parsed.path) or "/"
    # Strip trailing slash if path is longer than root '/'
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    # Strip tracking parameters and sort remainder deterministically
    query_params = parse_qsl(parsed.query, keep_blank_values=True)
    filtered_params = [
        (k, v)
        for k, v in query_params
        if k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_")
    ]
    filtered_params.sort(key=lambda x: x[0])
    clean_query = urlencode(filtered_params)

    # Drop fragment completely
    clean_url = urlunparse((scheme, netloc, path, "", clean_query, ""))
    return clean_url


def generate_content_hash(canonical_url: str, title: str, content: str = "") -> str:
    """Generate a deterministic SHA-256 fingerprint hash for an article."""
    norm_url = canonical_url.strip()
    norm_title = title.strip().lower()
    norm_content = (content or "").strip()[:2000]

    hasher = hashlib.sha256()
    hasher.update(norm_url.encode("utf-8"))
    hasher.update(b"|")
    hasher.update(norm_title.encode("utf-8"))
    hasher.update(b"|")
    hasher.update(norm_content.encode("utf-8"))
    return hasher.hexdigest()
