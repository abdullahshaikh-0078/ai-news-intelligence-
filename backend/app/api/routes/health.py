from datetime import datetime, timezone
from typing import Dict, Optional
from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.infrastructure.database.session import get_db, ping_database

router = APIRouter(tags=["Health & Readiness"])


class HealthCheckResponse(BaseModel):
    status: str = Field(..., json_schema_extra={"example": "healthy"})
    app_name: str = Field(..., json_schema_extra={"example": "AI News Intelligence Platform"})
    version: str = Field(..., json_schema_extra={"example": "0.1.0"})
    environment: str = Field(..., json_schema_extra={"example": "development"})
    timestamp: str = Field(...)
    checks: Dict[str, str] = Field(default_factory=dict)


class ReadinessResponse(BaseModel):
    status: str = Field(..., json_schema_extra={"example": "ready"})
    app_name: str = Field(..., json_schema_extra={"example": "AI News Intelligence Platform"})
    version: str = Field(..., json_schema_extra={"example": "0.1.0"})
    environment: str = Field(..., json_schema_extra={"example": "development"})
    timestamp: str = Field(...)
    checks: Dict[str, str] = Field(default_factory=dict)
    error: Optional[str] = None


@router.get(
    "/health",
    response_model=HealthCheckResponse,
    summary="Liveness Probe",
    description="Determines if the application process is running and responding to HTTP requests.",
)
async def get_health() -> HealthCheckResponse:
    """Liveness probe returning platform operational status."""
    return HealthCheckResponse(
        status="healthy",
        app_name=settings.APP_NAME,
        version=settings.APP_VERSION,
        environment=settings.ENVIRONMENT,
        timestamp=datetime.now(timezone.utc).isoformat(),
        checks={
            "api": "operational",
            "environment": settings.ENVIRONMENT,
        },
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness Probe",
    description="Verifies that all core dependencies (including PostgreSQL) are operational and ready to accept traffic.",
)
async def get_readiness(
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> ReadinessResponse:
    """Readiness probe verifying PostgreSQL connectivity."""
    db_ok, error_detail = await ping_database(db)

    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(
            status="degraded",
            app_name=settings.APP_NAME,
            version=settings.APP_VERSION,
            environment=settings.ENVIRONMENT,
            timestamp=datetime.now(timezone.utc).isoformat(),
            checks={
                "api": "operational",
                "database": "unavailable",
            },
            error="Database connection failure",
        )

    return ReadinessResponse(
        status="ready",
        app_name=settings.APP_NAME,
        version=settings.APP_VERSION,
        environment=settings.ENVIRONMENT,
        timestamp=datetime.now(timezone.utc).isoformat(),
        checks={
            "api": "operational",
            "database": "connected",
        },
    )
