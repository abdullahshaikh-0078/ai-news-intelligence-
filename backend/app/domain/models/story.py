from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field


class StoryStatus(str, Enum):
    """Lifecycle states for clustered stories."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    UPDATED = "UPDATED"
    ARCHIVED = "ARCHIVED"


class StoryItemEvidence(BaseModel):
    """Provenance and summary data for an individual ContentItem linked to a Story."""

    content_id: UUID
    source_name: str
    title: str
    canonical_url: str
    content_type: str
    published_at: Optional[datetime] = None
    is_canonical: bool = False
    confidence_score: float = 1.0

    model_config = ConfigDict(from_attributes=True)


class StoryDomain(BaseModel):
    """Domain representation of a clustered, synthesized event/story."""

    id: UUID = Field(default_factory=uuid4)
    headline: str
    summary: Optional[str] = None
    key_takeaway: Optional[str] = None
    why_it_matters: Optional[str] = None
    importance_score: float = 0.0
    category: Optional[str] = None
    is_curated: bool = False
    published_at: Optional[datetime] = None
    status: StoryStatus = StoryStatus.ACTIVE
    canonical_content_item_id: Optional[UUID] = None
    metadata_json: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class StoryDetailResponse(BaseModel):
    """Normalized response payload for a Story entity."""

    id: UUID
    title: str
    summary: Optional[str] = None
    key_takeaway: Optional[str] = None
    why_it_matters: Optional[str] = None
    status: str
    category: Optional[str] = None
    importance_score: float = 0.0
    ranking_score: float = 0.0
    is_curated: bool = False
    canonical_content_item_id: Optional[UUID] = None
    article_count: int = 0
    published_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class StoryListResponse(BaseModel):
    """Standardized paginated list container for stories."""

    items: List[StoryDetailResponse]
    page: int
    page_size: int
    total: int
    total_pages: int


class StoryWithItemsResponse(StoryDetailResponse):
    """Extended Story response including all associated source evidence items."""

    items: List[StoryItemEvidence] = Field(default_factory=list)


class StoryClusterRequest(BaseModel):
    """Parameters for executing a bounded story clustering pass."""

    limit: int = Field(default=50, ge=1, le=100, description="Max unclustered candidates to evaluate.")
    window_hours: Optional[int] = Field(default=None, ge=1, le=720, description="Temporal window for clustering in hours.")
    similarity_threshold: Optional[float] = Field(default=None, ge=0.5, le=1.0, description="Cosine similarity cutoff.")


class StoryClusterResponse(BaseModel):
    """Telemetry returned from a story clustering run."""

    candidates_scanned: int
    stories_created: int
    stories_updated: int
    items_assigned: int
    items_skipped_no_embedding: int


class StoryRefreshResponse(BaseModel):
    """Response returned upon refreshing a story's synthesis and canonical item."""

    story_id: UUID
    title: str
    summary: Optional[str] = None
    article_count: int
    status: str
