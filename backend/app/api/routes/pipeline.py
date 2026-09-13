from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.pipeline import (
    PipelineRunRequest,
    PipelineRunResponse,
    PipelineStatusResponse,
)
from app.infrastructure.database.session import get_db
from app.services.pipeline_service import PipelineOrchestratorService

router = APIRouter(prefix="/pipeline", tags=["Pipeline Orchestration"])


@router.post(
    "/run",
    response_model=PipelineRunResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute bounded end-to-end AI News processing pipeline",
)
async def run_pipeline(
    request: PipelineRunRequest = PipelineRunRequest(),
    session: AsyncSession = Depends(get_db),
) -> PipelineRunResponse:
    """
    Execute a controlled, bounded, end-to-end AI News processing run.

    Coordinates all pipeline stages:
      1. Ingestion (optional)
      2. Candidate Selection & Normalization
      3. Deterministic Deduplication
      4. AI Analysis (LLM extraction with quota control & idempotency)
      5. On-Demand Embedding (reuses existing, authentic Gemini vectors only, never mock)
      6. Semantic Deduplication (pgvector cosine similarity)
      7. Story Clustering & Grounded Synthesis
      8. Story Ranking (6 transparent signals)
      9. Editorial Curation (diversity constraints)
      10. Digest-Ready Candidate Stories Compilation (top 5–10)

    NOTE: This endpoint is intended for internal/admin pipeline orchestration.
    It enforces strict limits and is safe to call repeatedly.
    """
    service = PipelineOrchestratorService(session=session)
    return await service.run_pipeline(request)


@router.get(
    "/status",
    response_model=PipelineStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Get pipeline orchestration status and queue limits",
)
async def get_pipeline_status(
    session: AsyncSession = Depends(get_db),
) -> PipelineStatusResponse:
    """
    Retrieve current pipeline configuration limits, active AI provider configuration,
    and database queue status counts.
    """
    service = PipelineOrchestratorService(session=session)
    return await service.get_pipeline_status()
