import pytest
import uuid
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_api_create_source_success(async_client: AsyncClient):
    """Test successful source creation via POST /api/v1/sources."""
    tag = uuid.uuid4().hex[:6]
    payload = {
        "name": f"Hugging Face Blog {tag}",
        "type": "RSS",
        "url": f"https://huggingface.co/blog/feed.xml?tag={tag}",
        "enabled": True,
        "language": "en",
        "reliability_score": 0.95,
        "fetch_interval_minutes": 60,
        "configuration": {"tags": ["NLP", "Transformers"]},
    }
    response = await async_client.post("/api/v1/sources", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == f"Hugging Face Blog {tag}"
    assert data["type"] == "RSS"
    assert data["enabled"] is True
    assert data["reliability_score"] == 0.95
    assert data["fetch_interval_minutes"] == 60
    assert data["configuration"] == {"tags": ["NLP", "Transformers"]}
    assert "id" in data
    assert "slug" in data


@pytest.mark.asyncio
async def test_api_create_source_validation_errors(async_client: AsyncClient):
    """Test 422 errors on invalid request payloads."""
    # Invalid type
    res = await async_client.post(
        "/api/v1/sources",
        json={"name": "Bad Type", "type": "NOT_A_TYPE", "url": "https://example.com"},
    )
    assert res.status_code == 422

    # Invalid URL
    res = await async_client.post(
        "/api/v1/sources",
        json={"name": "Bad URL", "type": "RSS", "url": "not-a-valid-url"},
    )
    assert res.status_code == 422

    # Out of range reliability score (> 1.0)
    res = await async_client.post(
        "/api/v1/sources",
        json={"name": "Bad Score", "type": "RSS", "url": "https://example.com/feed", "reliability_score": 2.5},
    )
    assert res.status_code == 422

    # Fetch interval too low (< 5 min)
    res = await async_client.post(
        "/api/v1/sources",
        json={"name": "Too Low Interval", "type": "RSS", "url": "https://example.com/feed", "fetch_interval_minutes": 1},
    )
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_api_duplicate_url_conflict(async_client: AsyncClient):
    """Test 409 Conflict when creating a source with existing URL."""
    shared_url = f"https://example.com/duplicate-{uuid.uuid4().hex[:8]}"
    payload1 = {"name": "First Source", "type": "WEB", "url": shared_url}
    res1 = await async_client.post("/api/v1/sources", json=payload1)
    assert res1.status_code == 201

    payload2 = {"name": "Second Source", "type": "WEB", "url": shared_url}
    res2 = await async_client.post("/api/v1/sources", json=payload2)
    assert res2.status_code == 409
    assert res2.json()["error"]["code"] == "CONFLICT"


@pytest.mark.asyncio
async def test_api_list_sources_and_filtering(async_client: AsyncClient):
    """Test GET /api/v1/sources with type and enabled filters."""
    tag = uuid.uuid4().hex[:6]
    # Create 1 GITHUB source and 1 YOUTUBE source
    await async_client.post(
        "/api/v1/sources",
        json={"name": f"GH {tag}", "type": "GITHUB", "url": f"https://github.com/test-{tag}", "enabled": True},
    )
    await async_client.post(
        "/api/v1/sources",
        json={"name": f"YT {tag}", "type": "YOUTUBE", "url": f"https://youtube.com/test-{tag}", "enabled": False},
    )

    # Filter by type=GITHUB
    res = await async_client.get("/api/v1/sources?type=GITHUB")
    assert res.status_code == 200
    data = res.json()
    assert "items" in data
    assert "total" in data
    assert all(item["type"] == "GITHUB" for item in data["items"])

    # Filter by enabled=false
    res_disabled = await async_client.get("/api/v1/sources?enabled=false")
    assert res_disabled.status_code == 200
    data_disabled = res_disabled.json()
    assert all(item["enabled"] is False for item in data_disabled["items"])


@pytest.mark.asyncio
async def test_api_get_and_patch_source(async_client: AsyncClient):
    """Test GET and PATCH by ID."""
    tag = uuid.uuid4().hex[:6]
    create_res = await async_client.post(
        "/api/v1/sources",
        json={"name": f"Patch Target {tag}", "type": "RSS", "url": f"https://example.com/patch-{tag}"},
    )
    source_id = create_res.json()["id"]

    # Get by ID
    get_res = await async_client.get(f"/api/v1/sources/{source_id}")
    assert get_res.status_code == 200
    assert get_res.json()["id"] == source_id

    # Patch
    patch_res = await async_client.patch(
        f"/api/v1/sources/{source_id}",
        json={"name": f"Patched Name {tag}", "reliability_score": 0.75, "fetch_interval_minutes": 120},
    )
    assert patch_res.status_code == 200
    patched_data = patch_res.json()
    assert patched_data["name"] == f"Patched Name {tag}"
    assert patched_data["reliability_score"] == 0.75
    assert patched_data["fetch_interval_minutes"] == 120


@pytest.mark.asyncio
async def test_api_enable_disable_toggle(async_client: AsyncClient):
    """Test POST /enable and /disable endpoints."""
    tag = uuid.uuid4().hex[:6]
    create_res = await async_client.post(
        "/api/v1/sources",
        json={"name": f"Toggle Target {tag}", "type": "ARXIV", "url": f"https://example.com/toggle-{tag}", "enabled": True},
    )
    source_id = create_res.json()["id"]

    # Disable
    dis_res = await async_client.post(f"/api/v1/sources/{source_id}/disable")
    assert dis_res.status_code == 200
    assert dis_res.json()["enabled"] is False

    # Enable
    en_res = await async_client.post(f"/api/v1/sources/{source_id}/enable")
    assert en_res.status_code == 200
    assert en_res.json()["enabled"] is True


@pytest.mark.asyncio
async def test_api_delete_source(async_client: AsyncClient):
    """Test DELETE /api/v1/sources/{id} and subsequent 404."""
    tag = uuid.uuid4().hex[:6]
    create_res = await async_client.post(
        "/api/v1/sources",
        json={"name": f"Delete Target {tag}", "type": "WEB", "url": f"https://example.com/delete-{tag}"},
    )
    source_id = create_res.json()["id"]

    # Delete
    del_res = await async_client.delete(f"/api/v1/sources/{source_id}")
    assert del_res.status_code == 204

    # Verify 404
    get_res = await async_client.get(f"/api/v1/sources/{source_id}")
    assert get_res.status_code == 404


@pytest.mark.asyncio
async def test_api_seed_endpoint(async_client: AsyncClient):
    """Test POST /api/v1/sources/seed returns baseline sources."""
    res = await async_client.post("/api/v1/sources/seed")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) >= 5
    types = {item["type"] for item in data}
    assert "RSS" in types
    assert "ARXIV" in types
    assert "WEB" in types
    assert "YOUTUBE" in types
    assert "GITHUB" in types
