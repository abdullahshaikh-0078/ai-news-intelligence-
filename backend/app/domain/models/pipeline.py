from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, Field


class PipelineFailureDetail(BaseModel):
    """Structured failure description for isolated item/stage errors."""
    item_id: Optional[UUID] = None
    stage: str
    error_type: str
    message: str


class CuratedStorySummary(BaseModel):
    """Digest-ready candidate story summary output by editorial curation."""
    story_id: UUID
    headline: str
    summary: str
    key_takeaway: Optional[str] = None
    why_it_matters: Optional[str] = None
    ranking_score: float
    category: Optional[str] = None
    canonical_url: Optional[str] = None
    primary_source_name: Optional[str] = None
    article_count: int = 1
    published_at: Optional[datetime] = None


class PipelineRunRequest(BaseModel):
    """Request parameters configuring a bounded pipeline run."""
    limit: Optional[int] = Field(
        None,
        ge=1,
        le=50,
        description="Max candidate items to inspect in this pass (default: settings.PIPELINE_MAX_ITEMS_PER_RUN)",
    )
    run_ingestion: bool = Field(
        False,
        description="Whether to execute live ingestion across registered sources prior to pipeline processing",
    )
    max_ai_analyses: Optional[int] = Field(
        None,
        ge=0,
        le=20,
        description="Max LLM analysis calls permitted in this run (default: settings.PIPELINE_MAX_AI_ANALYSES_PER_RUN)",
    )
    max_embeddings: Optional[int] = Field(
        None,
        ge=0,
        le=20,
        description="Max on-demand embedding calls permitted in this run (default: settings.PIPELINE_MAX_EMBEDDINGS_PER_RUN)",
    )
    curation_limit: Optional[int] = Field(
        None,
        ge=1,
        le=20,
        description="Max top stories to select in final curated feed (default: settings.PIPELINE_MAX_STORIES_PER_CURATED_FEED)",
    )
    force: bool = Field(
        False,
        description="Whether to force re-processing of items already marked COMPLETED or with existing embeddings",
    )
    min_relevance_for_embedding: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Minimum AI relevance score required for an item to receive an on-demand embedding",
    )


class PipelineRunResponse(BaseModel):
    """Comprehensive execution telemetry returned after an orchestrated pipeline pass."""
    status: str = Field(..., description="Overall pipeline status: SUCCESS, PARTIAL, or FAILED")
    items_seen: int = Field(0, description="Total candidate items examined")
    items_processed: int = Field(0, description="Total items that completed processing stages")
    ai_analyses_performed: int = Field(0, description="Authentic LLM text analyses conducted")
    embeddings_generated: int = Field(0, description="Authentic embeddings generated on demand")
    embeddings_reused: int = Field(0, description="Existing pre-computed embeddings reused (0 API calls)")
    deterministic_duplicates_found: int = Field(0, description="Exact identity matches detected via content hash or URL")
    semantic_duplicates_found: int = Field(0, description="Semantic duplicates identified via cosine distance")
    stories_created: int = Field(0, description="New multi-source stories formed")
    stories_updated: int = Field(0, description="Existing active stories updated with new member items")
    stories_ranked: int = Field(0, description="Stories evaluated across 6 transparent ranking signals")
    stories_curated: int = Field(0, description="Stories selected into final curated digest feed")
    failures: List[PipelineFailureDetail] = Field(default_factory=list, description="Isolated per-item/stage errors")
    curated_stories: List[CuratedStorySummary] = Field(default_factory=list, description="Top curated candidate stories")
    duration_ms: float = Field(0.0, description="Total pipeline execution duration in milliseconds")
    executed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PipelineStatusResponse(BaseModel):
    """Health, configuration limits, and queue status of the pipeline orchestrator."""
    status: str
    environment: str
    ai_provider: str
    chat_model: str
    embedding_model: str
    embedding_dimensions: int
    limits: Dict[str, Any]
    queue_summary: Dict[str, Any]
