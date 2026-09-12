from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field


class SourceType(str, Enum):
    """Supported upstream content source types."""

    RSS = "RSS"
    WEB = "WEB"
    ARXIV = "ARXIV"
    YOUTUBE = "YOUTUBE"
    GITHUB = "GITHUB"


class SourceDomain(BaseModel):
    """Domain representation of a content source registry item."""

    id: UUID = Field(default_factory=uuid4)
    name: str
    slug: str
    type: SourceType
    url: str
    enabled: bool = True
    language: str = "en"
    reliability_score: float = Field(default=1.0, ge=0.0, le=1.0)
    fetch_interval_minutes: int = Field(default=60, ge=5)
    last_fetched_at: Optional[datetime] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
