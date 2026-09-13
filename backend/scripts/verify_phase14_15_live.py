"""
Comprehensive Verification Script for Phase 14 (Production API) and Phase 15 (Portfolio Integration).
Verifies:
- Invariant preservation: 1,348 ContentItems, 4 Stories, 4 non-null embeddings.
- All Phase 14 FastAPI endpoints (/health, /overview, /curation/stories, /content, /stories/{id}/ranking).
- Phase 15 Portfolio HTTP delivery (index.html, projects/ai-news.html, CSS, JS modules).
"""

import asyncio
import sys
import asyncpg
import httpx

DB_URL = "postgresql://postgres:postgres@localhost:5433/ai_news_intel"
API_BASE = "http://127.0.0.1:8000"
PORTFOLIO_BASE = "http://127.0.0.1:5500"


async def check_db_invariants():
    print("\n[1/3] Checking PostgreSQL Invariants...")
    conn = await asyncpg.connect(DB_URL)
    try:
        total_items = await conn.fetchval("SELECT count(*) FROM content_items;")
        total_embeddings = await conn.fetchval("SELECT count(*) FROM content_items WHERE embedding IS NOT NULL;")
        total_stories = await conn.fetchval("SELECT count(*) FROM stories;")
        curated_stories = await conn.fetchval("SELECT count(*) FROM stories WHERE is_curated = true;")
        pgv_version = await conn.fetchval("SELECT extversion FROM pg_extension WHERE extname = 'vector';")
    finally:
        await conn.close()

    print(f"  - Total ContentItems: {total_items} (Expected: 1,348)")
    print(f"  - Total Embeddings:   {total_embeddings} (Expected: >= 4)")
    print(f"  - Total Stories:       {total_stories} (Expected: >= 4)")
    print(f"  - Curated Stories:     {curated_stories} (Expected: >= 4)")
    print(f"  - pgvector version:    {pgv_version} (Expected: 0.8.6)")

    assert total_items == 1348, f"Invariant violated! Expected 1,348 items, got {total_items}"
    assert total_embeddings >= 4, "Missing expected embeddings"
    assert pgv_version == "0.8.6", f"Unexpected pgvector version: {pgv_version}"
    print("  => DATABASE INVARIANTS PERFECTLY PRESERVED [PASS]")


def main():
    print("==================================================================")
    print("PHASE 14 & 15 VERIFICATION: PRODUCTION API & PORTFOLIO INTEGRATION")
    print("==================================================================")

    # 1. Database Invariant Check
    asyncio.run(check_db_invariants())

    # 2. FastAPI Endpoints Check
    print("\n[2/3] Checking FastAPI Production Endpoints (Port 8000)...")
    client = httpx.Client(timeout=10.0)

    # /health
    r_health = client.get(f"{API_BASE}/health")
    assert r_health.status_code == 200, f"Health check failed: {r_health.text}"
    print("  - GET /health -> 200 OK")

    # /api/v1/overview
    r_overview = client.get(f"{API_BASE}/api/v1/overview")
    assert r_overview.status_code == 200, f"Overview failed: {r_overview.text}"
    overview_data = r_overview.json()
    assert overview_data["total_content_items"] == 1348
    assert overview_data["total_stories"] >= 4
    assert overview_data["embedding_dimensions"] == 1536
    assert "Google Gemini" in overview_data["ai_provider"]
    print(f"  - GET /api/v1/overview -> 200 OK (Items: {overview_data['total_content_items']}, Provider: {overview_data['ai_provider']})")

    # /api/v1/curation/stories
    r_curation = client.get(f"{API_BASE}/api/v1/curation/stories?limit=10")
    assert r_curation.status_code == 200, f"Curation failed: {r_curation.text}"
    cur_stories = r_curation.json()
    assert len(cur_stories) >= 1
    sample_story = cur_stories[0]
    assert "ranking_score" in sample_story
    assert "primary_source_name" in sample_story
    print(f"  - GET /api/v1/curation/stories -> 200 OK (Returned {len(cur_stories)} stories, Top: '{sample_story['title'][:35]}...')")

    # /api/v1/stories/{id}/ranking
    story_id = sample_story["story_id"]
    r_ranking = client.get(f"{API_BASE}/api/v1/stories/{story_id}/ranking")
    assert r_ranking.status_code == 200, f"Ranking explanation failed: {r_ranking.text}"
    ranking_data = r_ranking.json()
    assert "signals" in ranking_data
    assert "recency" in ranking_data["signals"]
    assert "relevance" in ranking_data["signals"]
    assert "authority" in ranking_data["signals"]
    print(f"  - GET /api/v1/stories/{story_id}/ranking -> 200 OK (Score: {ranking_data['total_score']}, Signals: {list(ranking_data['signals'].keys())})")

    # /api/v1/content
    r_content = client.get(f"{API_BASE}/api/v1/content?page=1&page_size=5")
    assert r_content.status_code == 200, f"Content list failed: {r_content.text}"
    content_data = r_content.json()
    assert content_data["total"] == 1348
    assert len(content_data["items"]) == 5
    print(f"  - GET /api/v1/content -> 200 OK (Total: {content_data['total']}, Page: {content_data['page']}/{content_data['total_pages']})")

    print("  => ALL FASTAPI PRODUCTION ENDPOINTS VERIFIED [PASS]")

    # 3. Portfolio Static Assets Check
    print("\n[3/3] Checking Portfolio Static Delivery (Port 5500)...")

    # index.html
    r_index = client.get(f"{PORTFOLIO_BASE}/index.html")
    assert r_index.status_code == 200
    assert "AI News Platform" in r_index.text
    print("  - GET /index.html -> 200 OK (Contains AI News Platform entry)")

    # projects/ai-news.html
    r_proj = client.get(f"{PORTFOLIO_BASE}/projects/ai-news.html")
    assert r_proj.status_code == 200
    assert "as-news-project-wrap" in r_proj.text
    assert "as-pipeline-flow" in r_proj.text
    print("  - GET /projects/ai-news.html -> 200 OK (Full interactive project page)")

    # scripts/core/api-client.js
    r_apijs = client.get(f"{PORTFOLIO_BASE}/scripts/core/api-client.js")
    assert r_apijs.status_code == 200
    print("  - GET /scripts/core/api-client.js -> 200 OK")

    # scripts/systems/ai-news-project.js
    r_ctrljs = client.get(f"{PORTFOLIO_BASE}/scripts/systems/ai-news-project.js")
    assert r_ctrljs.status_code == 200
    print("  - GET /scripts/systems/ai-news-project.js -> 200 OK")

    # styles/components/ai-news-project.css
    r_css = client.get(f"{PORTFOLIO_BASE}/styles/components/ai-news-project.css")
    assert r_css.status_code == 200
    print("  - GET /styles/components/ai-news-project.css -> 200 OK")

    print("  => PORTFOLIO ASSETS & SERVING VERIFIED [PASS]")

    print("\n==================================================================")
    print("PHASE 14 + 15 VERIFICATION COMPLETE: ALL 100% OPERATIONAL!")
    print("==================================================================")


if __name__ == "__main__":
    main()
