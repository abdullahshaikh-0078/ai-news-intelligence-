from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


from app.domain.models.content_item import ContentType


class RawFeedEntry(BaseModel):
    """Intermediary representation of a raw feed entry extracted from RSS/Atom."""

    title: str
    link: str
    summary: Optional[str] = None
    content: Optional[str] = None
    author: Optional[str] = None
    published_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    external_id: Optional[str] = None
    categories: List[str] = Field(default_factory=list)
    raw_metadata: Dict[str, Any] = Field(default_factory=dict)


class NormalizedArticle(BaseModel):
    """Validated, canonicalized article ready for database persistence."""

    source_id: UUID
    canonical_url: str
    title: str
    summary: Optional[str] = None
    raw_content: Optional[str] = None
    author: Optional[str] = None
    published_at: datetime
    fetched_at: datetime
    external_id: Optional[str] = None
    language: str = "en"
    content_hash: str
    content_type: ContentType = ContentType.ARTICLE
    thumbnail_url: Optional[str] = None
    categories: List[str] = Field(default_factory=list)
    metadata_json: Dict[str, Any] = Field(default_factory=dict)


class SourceIngestionResult(BaseModel):
    """Execution metrics and status for a single source ingestion run."""

    source_id: UUID
    source_name: str
    source_type: str
    status: str  # SUCCESS, FAILED, PARTIAL
    entries_fetched: int = 0
    entries_inserted: int = 0
    entries_updated: int = 0
    entries_skipped: int = 0
    errors: List[str] = Field(default_factory=list)
    duration_seconds: float = 0.0

    model_config = ConfigDict(from_attributes=True)


class IngestionSummaryResponse(BaseModel):
    """Aggregated metrics summary for an ingestion operation across multiple sources."""

    total_sources: int
    succeeded_sources: int
    failed_sources: int
    total_fetched: int
    total_inserted: int
    total_updated: int
    total_skipped: int
    source_results: List[SourceIngestionResult] = Field(default_factory=list)
    total_duration_seconds: float = 0.0
