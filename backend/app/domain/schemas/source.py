from typing import Any, Dict, List, Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator
from app.domain.models.source import SourceType


class SourceBase(BaseModel):
    """Base fields shared across Source schemas."""

    name: str = Field(..., min_length=1, max_length=255, description="Human-readable name of the source")
    type: SourceType = Field(..., description="Source ingestion category (RSS, WEB, ARXIV, YOUTUBE, GITHUB)")
    url: HttpUrl = Field(..., description="Primary endpoint, feed, or canonical URL")
    enabled: bool = Field(default=True, description="Whether the source is active for scheduled ingestion")
    language: str = Field(default="en", min_length=2, max_length=10, description="ISO language code of source content")
    reliability_score: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Historical or configured reliability rating (0.0 to 1.0)",
    )
    fetch_interval_minutes: int = Field(
        default=60,
        ge=5,
        le=10080,
        description="Ingestion polling interval in minutes (min 5, max 10080 = 7 days)",
    )
    configuration: Dict[str, Any] = Field(
        default_factory=dict,
        description="Extensible source-specific parameters (e.g. channel_id, query_params, headers)",
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("Name cannot be empty or whitespace only")
        return trimmed

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: str) -> str:
        return v.strip().lower()


class SourceCreate(SourceBase):
    """Schema for registering a new content source."""

    slug: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Optional custom slug; if omitted, automatically generated from name",
    )


class SourceUpdate(BaseModel):
    """Schema for updating an existing source."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    type: Optional[SourceType] = None
    url: Optional[HttpUrl] = None
    enabled: Optional[bool] = None
    language: Optional[str] = Field(default=None, min_length=2, max_length=10)
    reliability_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    fetch_interval_minutes: Optional[int] = Field(default=None, ge=5, le=10080)
    configuration: Optional[Dict[str, Any]] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            trimmed = v.strip()
            if not trimmed:
                raise ValueError("Name cannot be empty or whitespace only")
            return trimmed
        return v

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return v.strip().lower()
        return v


class SourceResponse(BaseModel):
    """Schema representing an exported Source entity."""

    id: UUID
    name: str
    slug: str
    type: SourceType
    url: str
    enabled: bool
    language: str
    reliability_score: float
    fetch_interval_minutes: int
    last_fetched_at: Optional[datetime] = None
    configuration: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class SourceListResponse(BaseModel):
    """Paginated list response container for sources."""

    items: List[SourceResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
