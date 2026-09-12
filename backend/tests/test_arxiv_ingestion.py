import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from app.infrastructure.repositories.content_repo import ContentRepository
from app.infrastructure.repositories.source_repo import SourceRepository
from app.ingestion.http_client import FeedFetchError
from app.ingestion.orchestrator import IngestionOrchestrator
from app.services.source_service import SourceService

PAGE1_ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <title>ArXiv Query: cat:cs.AI</title>
  <entry>
    <id>http://arxiv.org/abs/2609.00001v1</id>
    <published>2026-09-01T10:00:00Z</published>
    <title>Autonomous Agent Reasoning Systems</title>
    <summary>Initial exploration of multi-agent cognitive architecture.</summary>
    <author><name>Dr. Ada Lovelace</name></author>
    <arxiv:primary_category term="cs.AI"/>
    <category term="cs.AI"/>
    <link href="http://arxiv.org/abs/2609.00001v1" rel="alternate"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2609.00002v1</id>
    <published>2026-09-02T11:00:00Z</published>
    <title>Efficient Latent Diffusion Models</title>
    <summary>Sub-quadratic attention mechanisms for diffusion.</summary>
    <author><name>Geoffrey Hinton</name></author>
    <arxiv:primary_category term="cs.LG"/>
    <category term="cs.LG"/>
    <link href="http://arxiv.org/abs/2609.00002v1" rel="alternate"/>
  </entry>
</feed>
"""

# Version 2 update of paper 2609.00001
PAGE1_UPDATED_ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <title>ArXiv Query: cat:cs.AI</title>
  <entry>
    <id>http://arxiv.org/abs/2609.00001v2</id>
    <published>2026-09-01T10:00:00Z</published>
    <updated>2026-09-05T12:00:00Z</updated>
    <title>Autonomous Agent Reasoning Systems</title>
    <summary>Updated abstract: Expanded experimental results on benchmark suites.</summary>
    <author><name>Dr. Ada Lovelace</name></author>
    <author><name>Alan Turing</name></author>
    <arxiv:primary_category term="cs.AI"/>
    <category term="cs.AI"/>
    <link href="http://arxiv.org/abs/2609.00001v2" rel="alternate"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2609.00002v1</id>
    <published>2026-09-02T11:00:00Z</published>
    <title>Efficient Latent Diffusion Models</title>
    <summary>Sub-quadratic attention mechanisms for diffusion.</summary>
    <author><name>Geoffrey Hinton</name></author>
    <arxiv:primary_category term="cs.LG"/>
    <category term="cs.LG"/>
    <link href="http://arxiv.org/abs/2609.00002v1" rel="alternate"/>
  </entry>
</feed>
"""

PAGE2_ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <title>ArXiv Query: cat:cs.AI Page 2</title>
  <entry>
    <id>http://arxiv.org/abs/2609.00003v1</id>
    <published>2026-09-03T12:00:00Z</published>
    <title>Mathematical Foundations of Transformer Attention</title>
    <summary>Rigorous theoretical bounds on expressivity.</summary>
    <author><name>John von Neumann</name></author>
    <arxiv:primary_category term="cs.AI"/>
    <category term="cs.AI"/>
    <link href="http://arxiv.org/abs/2609.00003v1" rel="alternate"/>
  </entry>
</feed>
"""


@pytest.mark.asyncio
async def test_arxiv_source_seeding(db_session: AsyncSession):
    """Verify that ArXiv source is seeded with proper AI research configuration."""
    source_repo = SourceRepository(db_session)
    service = SourceService(source_repo)

    seeded = await service.seed_default_sources()
    slug_map = {s.slug: s for s in seeded}

    assert "arxiv-ai" in slug_map
    arxiv_src = slug_map["arxiv-ai"]
    assert arxiv_src.type == "ARXIV"
    assert arxiv_src.reliability_score == 0.95
    assert "cs.AI" in arxiv_src.config.get("search_query", "")


@pytest.mark.asyncio
async def test_arxiv_ingestion_and_idempotency(db_session: AsyncSession, monkeypatch):
    """Verify end-to-end ArXiv paper ingestion and zero-write idempotency."""
    source_repo = SourceRepository(db_session)
    content_repo = ContentRepository(db_session)
    service = SourceService(source_repo)
    seeded = await service.seed_default_sources()
    arxiv_src = next(s for s in seeded if s.slug == "arxiv-ai")

    async def mock_fetch(self, url):
        return PAGE1_ARXIV_XML

    monkeypatch.setattr("app.ingestion.http_client.FeedHttpClient.fetch", mock_fetch)

    orchestrator = IngestionOrchestrator(db_session)

    # 1. First Ingestion Run on arxiv_src
    res1 = await orchestrator.ingest_source(arxiv_src)
    assert res1.status == "SUCCESS"
    assert res1.entries_fetched == 2
    assert res1.entries_inserted == 2
    assert res1.entries_skipped == 0

    # Verify persisted paper in database
    item1 = await content_repo.get_by_canonical_url("https://arxiv.org/abs/2609.00001")
    assert item1 is not None
    assert item1.external_id == "2609.00001"
    assert item1.title == "Autonomous Agent Reasoning Systems"
    assert "multi-agent cognitive architecture" in item1.summary
    assert item1.author == "Dr. Ada Lovelace"

    # 2. Second Ingestion Run -> Strict Idempotency Check
    res2 = await orchestrator.ingest_source(arxiv_src)
    assert res2.status == "SUCCESS"
    assert res2.entries_fetched == 2
    assert res2.entries_inserted == 0
    assert res2.entries_skipped == 2

    # 3. Batch Ingestion Check
    summary = await orchestrator.ingest_all_arxiv_sources()
    assert summary.succeeded_sources >= 1
    assert summary.failed_sources == 0


@pytest.mark.asyncio
async def test_arxiv_paper_version_update(db_session: AsyncSession, monkeypatch):
    """Verify that a paper update (v1 -> v2) updates existing record without duplicate rows."""
    source_repo = SourceRepository(db_session)
    content_repo = ContentRepository(db_session)
    service = SourceService(source_repo)
    seeded = await service.seed_default_sources()
    arxiv_src = next(s for s in seeded if s.slug == "arxiv-ai")

    # Step 1: Ingest v1
    async def mock_fetch_v1(self, url):
        return PAGE1_ARXIV_XML

    monkeypatch.setattr("app.ingestion.http_client.FeedHttpClient.fetch", mock_fetch_v1)
    orchestrator = IngestionOrchestrator(db_session)
    res1 = await orchestrator.ingest_source(arxiv_src)
    assert res1.entries_inserted == 2

    # Step 2: Ingest v2 (updated abstract and second author)
    async def mock_fetch_v2(self, url):
        return PAGE1_UPDATED_ARXIV_XML

    monkeypatch.setattr("app.ingestion.http_client.FeedHttpClient.fetch", mock_fetch_v2)
    res2 = await orchestrator.ingest_source(arxiv_src)

    # 1 updated (2609.00001), 1 skipped (2609.00002), 0 new inserted rows!
    assert res2.entries_inserted == 0
    assert res2.entries_updated == 1
    assert res2.entries_skipped == 1

    # Verify updated content in the same database row
    updated_item = await content_repo.get_by_canonical_url("https://arxiv.org/abs/2609.00001")
    assert updated_item is not None
    assert "Updated abstract: Expanded experimental results" in updated_item.summary
    assert updated_item.metadata_json["version"] == "v2"
    assert "Alan Turing" in updated_item.metadata_json["authors"]


@pytest.mark.asyncio
async def test_arxiv_pagination_handling(db_session: AsyncSession, monkeypatch):
    """Verify that multiple pages are fetched sequentially up to max_pages."""
    source_repo = SourceRepository(db_session)
    service = SourceService(source_repo)
    seeded = await service.seed_default_sources()
    arxiv_source = next(s for s in seeded if s.slug == "arxiv-ai")

    # Configure 2 pages of size 2
    arxiv_source.config = {
        **arxiv_source.config,
        "max_results_per_page": 2,
        "max_pages": 2,
        "rate_limit_delay_seconds": 0.01,
    }
    await source_repo.update(arxiv_source)

    async def mock_fetch(self, url):
        if "start=0" in url:
            return PAGE1_ARXIV_XML
        elif "start=2" in url:
            return PAGE2_ARXIV_XML
        return "<feed></feed>"

    monkeypatch.setattr("app.ingestion.http_client.FeedHttpClient.fetch", mock_fetch)

    orchestrator = IngestionOrchestrator(db_session)
    summary = await orchestrator.ingest_source(arxiv_source)

    # Both page 1 (2 entries) and page 2 (1 entry) should be inserted
    assert summary.status == "SUCCESS"
    assert summary.entries_fetched == 3
    assert summary.entries_inserted == 3
    assert summary.entries_skipped == 0


@pytest.mark.asyncio
async def test_arxiv_fault_isolation(db_session: AsyncSession, monkeypatch):
    """Verify that ArXiv API failure is isolated and does not halt orchestrator."""
    source_repo = SourceRepository(db_session)
    service = SourceService(source_repo)
    await service.seed_default_sources()

    async def mock_fetch(self, url):
        raise FeedFetchError("ArXiv API gateway timeout (504)")

    monkeypatch.setattr("app.ingestion.http_client.FeedHttpClient.fetch", mock_fetch)

    orchestrator = IngestionOrchestrator(db_session)
    summary = await orchestrator.ingest_all_arxiv_sources()

    assert summary.failed_sources >= 1
    assert summary.total_inserted == 0
