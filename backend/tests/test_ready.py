from unittest.mock import patch
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_readiness_probe_success(async_client: AsyncClient):
    """Test /ready probe when database is available."""
    response = await async_client.get("/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["checks"]["api"] == "operational"
    assert data["checks"]["database"] == "connected"
    assert data["error"] is None


@pytest.mark.asyncio
async def test_v1_readiness_probe_success(async_client: AsyncClient):
    """Test /api/v1/ready probe when database is available."""
    response = await async_client.get("/api/v1/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["checks"]["database"] == "connected"


@pytest.mark.asyncio
async def test_readiness_probe_database_unavailable(async_client: AsyncClient):
    """Test /ready probe returns 503 Service Unavailable when database connection fails."""
    with patch("app.api.routes.health.ping_database", return_value=(False, "ConnectionRefused")):
        response = await async_client.get("/ready")
        assert response.status_code == 503
        data = response.json()
        assert data["status"] == "degraded"
        assert data["checks"]["api"] == "operational"
        assert data["checks"]["database"] == "unavailable"
        assert "failure" in data["error"].lower()


@pytest.mark.asyncio
async def test_liveness_probe_still_healthy_when_database_down(async_client: AsyncClient):
    """Verify that liveness probe (/health) succeeds even if database is unavailable."""
    with patch("app.api.routes.health.ping_database", return_value=(False, "ConnectionRefused")):
        response = await async_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
