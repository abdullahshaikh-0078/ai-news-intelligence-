import math
from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.domain.models.source import SourceType
from app.domain.schemas.source import (
    SourceCreate,
    SourceListResponse,
    SourceResponse,
    SourceUpdate,
)
from app.infrastructure.database.session import get_db
from app.infrastructure.repositories.source_repo import SourceRepository
from app.services.source_service import SourceService

router = APIRouter(prefix="/sources", tags=["sources"])


def get_source_service(session: AsyncSession = Depends(get_db)) -> SourceService:
    """Dependency provider injecting SourceService with transactional session."""
    repository = SourceRepository(session)
    return SourceService(repository)


@router.post(
    "",
    response_model=SourceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new content ingestion source",
)
async def create_source(
    payload: SourceCreate,
    service: SourceService = Depends(get_source_service),
) -> SourceResponse:
    """Create a new source entity with unique URL validation and automatic slug generation."""
    source = await service.create_source(payload)
    return SourceResponse.model_validate(source)


@router.get(
    "",
    response_model=SourceListResponse,
    status_code=status.HTTP_200_OK,
    summary="List content sources with filtering and pagination",
)
async def list_sources(
    type: Optional[SourceType] = Query(None, description="Filter by source type (RSS, WEB, ARXIV, YOUTUBE, GITHUB)"),
    enabled: Optional[bool] = Query(None, description="Filter by enabled status"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Number of items per page"),
    service: SourceService = Depends(get_source_service),
) -> SourceListResponse:
    """Retrieve paginated content sources matching optional filter criteria."""
    items, total = await service.list_sources(
        source_type=type,
        enabled=enabled,
        page=page,
        page_size=page_size,
    )
    total_pages = math.ceil(total / page_size) if total > 0 else 0
    return SourceListResponse(
        items=[SourceResponse.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.post(
    "/seed",
    response_model=List[SourceResponse],
    status_code=status.HTTP_200_OK,
    summary="Seed baseline verified content sources",
)
async def seed_sources(
    service: SourceService = Depends(get_source_service),
) -> List[SourceResponse]:
    """Seed or verify baseline default sources (ArXiv, MIT Tech Review, OpenAI, Two Minute Papers, GitHub)."""
    seeded = await service.seed_default_sources()
    return [SourceResponse.model_validate(item) for item in seeded]


@router.get(
    "/{source_id}",
    response_model=SourceResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve source by UUID",
)
async def get_source(
    source_id: uuid.UUID,
    service: SourceService = Depends(get_source_service),
) -> SourceResponse:
    """Fetch details for a specific registered source."""
    source = await service.get_source(source_id)
    return SourceResponse.model_validate(source)


@router.patch(
    "/{source_id}",
    response_model=SourceResponse,
    status_code=status.HTTP_200_OK,
    summary="Update an existing content source",
)
async def update_source(
    source_id: uuid.UUID,
    payload: SourceUpdate,
    service: SourceService = Depends(get_source_service),
) -> SourceResponse:
    """Partially update source attributes such as URL, interval, reliability, or configuration."""
    source = await service.update_source(source_id, payload)
    return SourceResponse.model_validate(source)


@router.post(
    "/{source_id}/enable",
    response_model=SourceResponse,
    status_code=status.HTTP_200_OK,
    summary="Enable a content source",
)
async def enable_source(
    source_id: uuid.UUID,
    service: SourceService = Depends(get_source_service),
) -> SourceResponse:
    """Set enabled flag to True for the specified source."""
    source = await service.enable_source(source_id)
    return SourceResponse.model_validate(source)


@router.post(
    "/{source_id}/disable",
    response_model=SourceResponse,
    status_code=status.HTTP_200_OK,
    summary="Disable a content source",
)
async def disable_source(
    source_id: uuid.UUID,
    service: SourceService = Depends(get_source_service),
) -> SourceResponse:
    """Set enabled flag to False, preventing scheduled ingestion."""
    source = await service.disable_source(source_id)
    return SourceResponse.model_validate(source)


@router.delete(
    "/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a content source",
)
async def delete_source(
    source_id: uuid.UUID,
    service: SourceService = Depends(get_source_service),
) -> Response:
    """Delete a content source registry item."""
    await service.delete_source(source_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
