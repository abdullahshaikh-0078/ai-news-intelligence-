from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class DuplicateClassification(str, Enum):
    """Categorical confidence level of content duplication."""

    EXACT_DUPLICATE = "EXACT_DUPLICATE"
    HIGH_CONFIDENCE_DUPLICATE = "HIGH_CONFIDENCE_DUPLICATE"
    POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE"
    NOT_DUPLICATE = "NOT_DUPLICATE"


class DetectionMethod(str, Enum):
    """Underlying technique used to identify content duplication or similarity."""

    CANONICAL_URL = "CANONICAL_URL"
    CONTENT_HASH = "CONTENT_HASH"
    EXTERNAL_ID = "EXTERNAL_ID"
    SEMANTIC_SIMILARITY = "SEMANTIC_SIMILARITY"


class DuplicateMatch(BaseModel):
    """Normalized structured representation of an identified duplicate or similar item."""

    target_content_id: UUID = Field(description="Content item initiating or being evaluated.")
    matched_content_id: UUID = Field(description="Identified matching content item.")
    matched_title: str = Field(description="Title of the matching content item.")
    matched_url: str = Field(description="Canonical URL of the matching content item.")
    similarity_score: float = Field(ge=-1.0, le=1.0, description="Similarity metric (1.0 for exact, cosine similarity for vector).")
    cosine_distance: Optional[float] = Field(default=None, description="Native pgvector cosine distance (0.0 to 2.0) if vector comparison.")
    classification: DuplicateClassification = Field(description="Classification based on similarity thresholds.")
    detection_method: DetectionMethod = Field(description="Method used to detect relationship.")
    metadata_json: Dict[str, Any] = Field(default_factory=dict, description="Diagnostic and contextual metadata.")

    model_config = ConfigDict(from_attributes=True)


class ContentSimilarResponse(BaseModel):
    """Response payload for nearest-neighbor similarity search."""

    content_id: UUID
    has_embedding: bool
    total_matches: int
    matches: List[DuplicateMatch] = Field(default_factory=list)


class DuplicateCheckResponse(BaseModel):
    """Response payload for deterministic and semantic duplicate analysis of a single item."""

    content_id: UUID
    has_embedding: bool
    deterministic_duplicates: List[DuplicateMatch] = Field(default_factory=list)
    semantic_duplicates: List[DuplicateMatch] = Field(default_factory=list)
    top_classification: DuplicateClassification
    persisted: bool = False


class BatchDeduplicationRequest(BaseModel):
    """Request payload for bounded batch deduplication run."""

    limit: int = Field(default=50, ge=1, le=100, description="Max candidate items to analyze.")
    persist: bool = Field(default=True, description="Whether to persist identified duplicate pairs.")


class BatchDeduplicationResponse(BaseModel):
    """Aggregate metrics returned from a bounded batch deduplication run."""

    total_scanned: int
    items_with_embedding: int
    items_skipped_no_embedding: int
    duplicate_pairs_identified: int
    persisted_pairs_count: int
