from datetime import datetime
import math
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppException
from app.domain.schemas.content import ContentItemListResponse, ContentItemResponse
from app.infrastructure.database.session import get_db
from app.infrastructure.repositories.content_repo import ContentRepository

router = APIRouter(prefix="/content", tags=["Content Items"])


@router.get(
    "",
    response_model=ContentItemListResponse,
    status_code=status.HTTP_200_OK,
    summary="List ingested content items with pagination and filtering",
)
async def list_content(
    content_type: Optional[str] = Query(None, description="Filter by canonical ContentType (e.g. ARTICLE, RESEARCH_PAPER, VIDEO, COMMUNITY_POST)"),
    source_id: Optional[UUID] = Query(None, description="Filter by origin source ID"),
    published_after: Optional[datetime] = Query(None, description="Include items published on or after timestamp"),
    published_before: Optional[datetime] = Query(None, description="Include items published on or before timestamp"),
    order_by: str = Query("published_at_desc", description="Sort order: published_at_desc, published_at_asc, relevance_desc"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page (max 100)"),
    session: AsyncSession = Depends(get_db),
) -> ContentItemListResponse:
    """Retrieve normalized content items with source metadata, AI intelligence, and pagination."""
    repo = ContentRepository(session)
    items, total = await repo.list_content_paginated(
        content_type=content_type,
        source_id=source_id,
        published_after=published_after,
        published_before=published_before,
        order_by=order_by,
        page=page,
        page_size=page_size,
    )

    total_pages = math.ceil(total / page_size) if total > 0 else 1

    item_responses = [
        ContentItemResponse(
            id=it.id,
            source_id=it.source_id,
            source_name=it.source.name if it.source else "Unknown Source",
            title=it.title,
            canonical_url=it.canonical_url,
            content_type=it.content_type,
            author=it.author,
            published_at=it.published_at,
            fetched_at=it.fetched_at,
            ai_relevance_score=it.ai_relevance_score,
            ai_summary=it.ai_summary,
            ai_topics=it.ai_topics or [],
            processing_state=it.processing_state,
            metadata=it.metadata_json or {},
        )
        for it in items
    ]

    return ContentItemListResponse(
        items=item_responses,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
    )


@router.get(
    "/{content_id}",
    response_model=ContentItemResponse,
    status_code=status.HTTP_200_OK,
    summary="Get content item by ID",
)
async def get_content_item(
    content_id: UUID,
    session: AsyncSession = Depends(get_db),
) -> ContentItemResponse:
    """Retrieve detailed content item with full source provenance."""
    repo = ContentRepository(session)
    it = await repo.get_content_with_source(content_id)
    if not it:
        raise AppException("NOT_FOUND", f"Content item with ID {content_id} not found", status_code=404)

    return ContentItemResponse(
        id=it.id,
        source_id=it.source_id,
        source_name=it.source.name if it.source else "Unknown Source",
        title=it.title,
        canonical_url=it.canonical_url,
        content_type=it.content_type,
        author=it.author,
        published_at=it.published_at,
        fetched_at=it.fetched_at,
        ai_relevance_score=it.ai_relevance_score,
        ai_summary=it.ai_summary,
        ai_topics=it.ai_topics or [],
        processing_state=it.processing_state,
        metadata=it.metadata_json or {},
    )
