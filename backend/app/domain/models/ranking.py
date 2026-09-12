from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class RankingSignalExplanation(BaseModel):
    """Normalized explanation for an individual ranking signal."""

    name: str = Field(description="Signal identifier (e.g. relevance, authority, recency, etc.)")
    raw_value: float = Field(description="Raw signal value before normalization")
    normalized_value: float = Field(ge=0.0, le=1.0, description="Normalized score in range [0.0, 1.0]")
    weight: float = Field(ge=0.0, description="Configured weight for this signal")
    weighted_score: float = Field(ge=0.0, description="normalized_value * weight")
    details: Dict[str, Any] = Field(default_factory=dict, description="Additional context or unit annotations")


class StoryRankingExplanation(BaseModel):
    """Complete transparent ranking explanation for a story."""

    story_id: UUID
    headline: str
    total_score: float = Field(ge=0.0, le=1.0, description="Final weighted normalized ranking score")
    signals: Dict[str, RankingSignalExplanation] = Field(
        default_factory=dict,
        description="Detailed breakdown for each of the 6 objective signals",
    )
    evaluated_at: datetime
    weights_sum: float = 1.0


class StoryRankedResponse(BaseModel):
    """Summary response for a ranked story."""

    story_id: UUID
    title: str
    ranking_score: float = Field(ge=0.0, le=1.0)
    category: Optional[str] = None
    article_count: int = 0
    published_at: Optional[datetime] = None
    ranking_updated_at: Optional[datetime] = None
    explanation: Optional[StoryRankingExplanation] = None

    model_config = ConfigDict(from_attributes=True)


class StoryRankingRunRequest(BaseModel):
    """Parameters for triggering a bounded story ranking execution."""

    limit: int = Field(default=50, ge=1, le=200, description="Max stories to evaluate and rank")
    force_recalculate: bool = Field(default=False, description="Re-score stories even if recently ranked")


class StoryRankingRunResponse(BaseModel):
    """Telemetry returned after executing a story ranking run."""

    stories_scanned: int
    stories_ranked: int
    duration_ms: float
