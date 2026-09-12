from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class ContentItemResponse(BaseModel):
    """Normalized public representation of an ingested content item."""

    id: UUID
    source_id: UUID
    source_name: Optional[str] = None
    title: str
    canonical_url: str
    content_type: str
    author: Optional[str] = None
    published_at: datetime
    fetched_at: datetime
    ai_relevance_score: Optional[float] = None
    ai_summary: Optional[str] = None
    ai_topics: Optional[List[str]] = Field(default_factory=list)
    processing_state: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(from_attributes=True)


class ContentItemListResponse(BaseModel):
    """Standardized paginated list container for content items."""

    items: List[ContentItemResponse]
    page: int
    page_size: int
    total: int
    total_pages: int
