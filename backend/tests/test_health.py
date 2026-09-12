import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_root_endpoint(async_client: AsyncClient):
    """Test the root index endpoint."""
    response = await async_client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "service" in data
    assert "version" in data
    assert data["health_url"] == "/health"


@pytest.mark.asyncio
async def test_health_check_endpoint(async_client: AsyncClient):
    """Test the core GET /health endpoint."""
    response = await async_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "app_name" in data
    assert "version" in data
    assert "environment" in data
    assert "timestamp" in data
    assert data["checks"]["api"] == "operational"


@pytest.mark.asyncio
async def test_v1_health_check_endpoint(async_client: AsyncClient):
    """Test versioned GET /api/v1/health endpoint."""
    response = await async_client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
