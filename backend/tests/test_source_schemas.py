import pytest
from pydantic import ValidationError
from app.domain.models.source import SourceType
from app.domain.schemas.source import SourceCreate, SourceUpdate


def test_valid_source_create():
    """Verify valid source payload creation."""
    payload = SourceCreate(
        name="TechCrunch AI",
        type=SourceType.RSS,
        url="https://techcrunch.com/category/artificial-intelligence/feed/",
        enabled=True,
        language="en",
        reliability_score=0.88,
        fetch_interval_minutes=30,
        configuration={"category": "ai"},
    )
    assert payload.name == "TechCrunch AI"
    assert payload.type == SourceType.RSS
    assert str(payload.url).rstrip("/") == "https://techcrunch.com/category/artificial-intelligence/feed"
    assert payload.reliability_score == 0.88
    assert payload.fetch_interval_minutes == 30
    assert payload.configuration == {"category": "ai"}


def test_invalid_source_type_rejected():
    """Verify unknown source type is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        SourceCreate(
            name="Invalid Type Source",
            type="INVALID_TYPE",  # type: ignore
            url="https://example.com/rss",
        )
    assert "type" in str(exc_info.value)


def test_invalid_url_rejected():
    """Verify malformed or non-HTTP URL is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        SourceCreate(
            name="Bad URL",
            type=SourceType.WEB,
            url="not-a-valid-url",
        )
    assert "url" in str(exc_info.value)


def test_invalid_reliability_score_rejected():
    """Verify reliability score outside [0.0, 1.0] is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        SourceCreate(
            name="High Score",
            type=SourceType.RSS,
            url="https://example.com/feed",
            reliability_score=1.5,
        )
    assert "reliability_score" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info:
        SourceCreate(
            name="Negative Score",
            type=SourceType.RSS,
            url="https://example.com/feed",
            reliability_score=-0.1,
        )
    assert "reliability_score" in str(exc_info.value)


def test_invalid_fetch_interval_rejected():
    """Verify fetch interval below minimum (5 minutes) is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        SourceCreate(
            name="Too Fast Polling",
            type=SourceType.RSS,
            url="https://example.com/feed",
            fetch_interval_minutes=2,
        )
    assert "fetch_interval_minutes" in str(exc_info.value)


def test_empty_or_whitespace_name_rejected():
    """Verify empty or all-whitespace name is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        SourceCreate(
            name="   ",
            type=SourceType.RSS,
            url="https://example.com/feed",
        )
    assert "name" in str(exc_info.value)


def test_valid_source_update():
    """Verify valid partial update schema."""
    update = SourceUpdate(
        name="Updated Name",
        reliability_score=0.92,
        fetch_interval_minutes=45,
    )
    assert update.name == "Updated Name"
    assert update.reliability_score == 0.92
    assert update.fetch_interval_minutes == 45
    assert update.url is None
