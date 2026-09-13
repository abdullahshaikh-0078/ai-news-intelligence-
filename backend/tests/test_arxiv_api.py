import pytest
from httpx import AsyncClient
from app.infrastructure.repositories.source_repo import SourceRepository
from app.services.source_service import SourceService

PAGE1_ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <title>ArXiv Query</title>
  <entry>
    <id>http://arxiv.org/abs/2609.99999v1</id>
    <published>2026-09-01T10:00:00Z</published>
    <title>Quantum Generative Pretraining</title>
    <summary>Exploring quantum circuits for sequence modeling.</summary>
    <author><name>Richard Feynman</name></author>
    <arxiv:primary_category term="cs.AI"/>
    <category term="cs.AI"/>
    <link href="http://arxiv.org/abs/2609.99999v1" rel="alternate"/>
  </entry>
</feed>
"""


@pytest.mark.asyncio
async def test_api_trigger_arxiv_batch_ingestion(async_client: AsyncClient, monkeypatch):
    """Test POST /api/v1/ingestion/arxiv batch route."""
    # Seed baseline sources via API
    await async_client.post("/api/v1/sources/seed")

    async def mock_fetch(self, url):
        return PAGE1_ARXIV_XML

    monkeypatch.setattr("app.ingestion.http_client.FeedHttpClient.fetch", mock_fetch)

    response = await async_client.post("/api/v1/ingestion/arxiv")
    assert response.status_code == 200
    data = response.json()
    assert data["total_sources"] >= 1
    assert data["succeeded_sources"] >= 1
    assert data["total_inserted"] >= 1


@pytest.mark.asyncio
async def test_api_trigger_single_arxiv_source(async_client: AsyncClient, monkeypatch):
    """Test POST /api/v1/ingestion/sources/{source_id} for an ArXiv source."""
    seed_res = await async_client.post("/api/v1/sources/seed")
    sources = seed_res.json()
    arxiv_src = next((s for s in sources if s["slug"] == "arxiv-ai"), None)
    assert arxiv_src is not None

    async def mock_fetch(self, url):
        return PAGE1_ARXIV_XML

    monkeypatch.setattr("app.ingestion.http_client.FeedHttpClient.fetch", mock_fetch)

    response = await async_client.post(f"/api/v1/ingestion/sources/{arxiv_src['id']}")
    assert response.status_code == 200
    data = response.json()
    assert data["source_id"] == arxiv_src["id"]
    assert data["source_type"] == "ARXIV"
    assert data["status"] == "SUCCESS"
    assert data["entries_fetched"] == 1
