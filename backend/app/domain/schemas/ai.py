from datetime import datetime
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class AIProcessRequest(BaseModel):
    """Request payload for processing a single content item."""

    force: bool = Field(default=False, description="Whether to reprocess an already completed item.")


class AIBatchProcessRequest(BaseModel):
    """Request payload for batch processing pending content items."""

    limit: int = Field(default=50, ge=1, le=500, description="Maximum number of items to process in this batch.")
    force: bool = Field(default=False, description="Whether to reprocess already completed items.")


class AIProcessResponse(BaseModel):
    """Response payload after processing a content item."""

    content_id: UUID
    title: str
    status: str
    ai_summary: Optional[str] = None
    ai_key_points: List[str] = Field(default_factory=list)
    ai_topics: List[str] = Field(default_factory=list)
    ai_relevance_score: Optional[float] = None
    ai_model: Optional[str] = None
    has_embedding: bool = False
    embedding_dim: Optional[int] = None
    ai_processed_at: Optional[datetime] = None
    message: str = "Success"

    model_config = ConfigDict(from_attributes=True)


class AIBatchProcessResponse(BaseModel):
    """Aggregate summary response for batch AI processing."""

    total_candidates: int
    completed: int
    skipped: int
    failed: int
    errors: List[str] = Field(default_factory=list)


class AIStatusSummaryResponse(BaseModel):
    """System-wide summary of AI processing states across all content items."""

    pending: int = 0
    processing: int = 0
    completed: int = 0
    failed: int = 0
    total: int = 0
