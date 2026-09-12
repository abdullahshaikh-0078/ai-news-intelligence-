from datetime import datetime
from typing import Dict, Optional
from pydantic import BaseModel, ConfigDict, Field


class OverviewResponse(BaseModel):
    """Real-time system overview statistics calculated directly from the database."""

    total_content_items: int = Field(description="Total ingested content items across all sources")
    total_stories: int = Field(description="Total clustered and synthesized story groups")
    active_sources: int = Field(description="Number of currently enabled ingestion sources")
    curated_stories: int = Field(description="Number of stories currently flagged for editorial feed")
    content_type_distribution: Dict[str, int] = Field(
        default_factory=dict,
        description="Distribution of content items by canonical ContentType",
    )
    latest_ingested_at: Optional[datetime] = Field(None, description="Timestamp of most recently ingested content item")
    latest_story_at: Optional[datetime] = Field(None, description="Timestamp of most recent story development")
    embedding_dimensions: int = Field(default=1536, description="Native pgvector embedding dimensionality")
    ai_provider: str = Field(default="Google Gemini (gemini-3.6-flash / gemini-embedding-001)")
    version: str = Field(default="0.1.0")

    model_config = ConfigDict(from_attributes=True)
