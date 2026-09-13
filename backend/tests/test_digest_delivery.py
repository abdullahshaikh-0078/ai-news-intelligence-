from datetime import date, datetime, timedelta, timezone
import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models.curation import CuratedStoryResponse
from app.domain.models.newsletter import SubscribeRequest
from app.infrastructure.models.digest import Digest
from app.infrastructure.repositories.digest_repo import DigestRepository
from app.services.digest_service import DigestService
from app.services.email_service import DeliveryService, MockEmailProvider
from app.services.subscriber_service import SubscriberService


@pytest.mark.asyncio
async def test_digest_generation_and_idempotency(db_session: AsyncSession):
    """Test deterministic daily digest generation and date-based idempotency."""
    digest_service = DigestService(session=db_session)
    target_date = date(2026, 9, 11)

    # 1. First generation
    digest1 = await digest_service.generate_daily_digest(target_date=target_date, force=False)
    assert digest1 is not None
    assert digest1.digest_date == target_date
    assert digest1.title.startswith("AI News Daily Digest")
    assert "DAILY DIGEST" in digest1.html_content
    # Stories count should reflect available curated stories (0 to 10)
    assert 0 <= digest1.story_count <= 10

    # 2. Second generation without force -> returns identical entity (idempotency)
    digest2 = await digest_service.generate_daily_digest(target_date=target_date, force=False)
    assert digest2.id == digest1.id
    assert digest2.story_count == digest1.story_count

    # 3. Force regeneration
    digest3 = await digest_service.generate_daily_digest(target_date=target_date, force=True)
    assert digest3.id == digest1.id


@pytest.mark.asyncio
async def test_digest_rendering_content_safety():
    """Verify rendered HTML and plain text newsletters adhere to consumer quality without internal DB leaks."""
    service = DigestService(session=None)  # Session not needed for pure rendering helper

    mock_stories = [
        CuratedStoryResponse(
            story_id=uuid.uuid4(),
            title="Google DeepMind Announces Breakthrough in Gemini Agents",
            summary="New autonomous capabilities enable multi-agent tool execution.",
            key_takeaway="Significant advancement in reasoning benchmarks.",
            why_it_matters="Accelerates autonomous development pipelines.",
            ranking_score=0.95,
            category="AI Agents",
            topics=["AI Agents", "Deep Learning"],
            canonical_url="https://deepmind.google/blog/breakthrough-agents",
            content_type="ARTICLE",
            primary_source_name="Google AI",
            article_count=3,
        ),
        CuratedStoryResponse(
            story_id=uuid.uuid4(),
            title="Anthropic Releases Research on Mechanistic Interpretability",
            summary="Insights into transformer inner representation layers.",
            key_takeaway="Improves model safety auditing.",
            why_it_matters="Critical for reliable alignment.",
            ranking_score=0.88,
            category="Research",
            topics=["Research", "Interpretability"],
            canonical_url="https://anthropic.com/research/interpretability",
            content_type="PAPER",
            primary_source_name="Anthropic",
            article_count=2,
        ),
    ]

    html_out = service.render_html_digest(
        title="AI News Daily Digest — September 11, 2026",
        issue_date=date(2026, 9, 11),
        stories=mock_stories,
    )
    plain_out = service.render_plain_text_digest(
        title="AI News Daily Digest — September 11, 2026",
        issue_date=date(2026, 9, 11),
        stories=mock_stories,
    )

    # HTML verification
    assert "Google DeepMind Announces Breakthrough" in html_out
    assert "https://deepmind.google/blog/breakthrough-agents" in html_out
    assert "Why it matters" in html_out
    assert "Read Full Source &rarr;" in html_out
    assert "{{ unsubscribe_url }}" in html_out
    # Consumer quality: No internal database schema names or debug text
    assert "pgvector" not in html_out.lower()
    assert "1348" not in html_out
    assert "content_items" not in html_out

    # Plain text verification
    assert "1. Google DeepMind Announces Breakthrough" in plain_out
    assert "Source: Google AI" in plain_out
    assert "Unsubscribe: {{ unsubscribe_url }}" in plain_out


@pytest.mark.asyncio
async def test_delivery_service_dispatch_and_idempotency(db_session: AsyncSession):
    """Test newsletter delivery, mock provider execution, and delivery idempotency."""
    subscriber_service = SubscriberService(session=db_session)
    digest_service = DigestService(session=db_session)

    # Register two active subscribers
    sub1_email = f"deliver1_{uuid.uuid4().hex[:8]}@example.com"
    sub2_email = f"deliver2_{uuid.uuid4().hex[:8]}@example.com"
    await subscriber_service.subscribe(SubscribeRequest(email=sub1_email, name="User One"))
    await subscriber_service.subscribe(SubscribeRequest(email=sub2_email, name="User Two"))

    # Generate a digest issue
    unique_date = date(2026, 8, 1) + timedelta(days=uuid.uuid4().int % 100)
    digest = await digest_service.generate_daily_digest(target_date=unique_date)

    mock_provider = MockEmailProvider(should_succeed=True)
    delivery_service = DeliveryService(session=db_session, email_provider=mock_provider)

    # 1. First send attempt
    result = await delivery_service.send_digest(digest_id=digest.id, dry_run=False)
    assert result.digest_id == digest.id
    assert result.sent_count >= 2
    assert result.failed_count == 0
    assert result.skipped_count == 0
    assert len(mock_provider.sent_messages) >= 2

    # Check delivery records in database
    digest_repo = DigestRepository(db_session)
    deliveries = await digest_repo.list_deliveries_for_digest(digest.id)
    assert len(deliveries) >= 2
    for d in deliveries:
        assert d.status == "SENT"
        assert d.provider == "MOCK"
        assert d.provider_message_id.startswith("mock_")

    # 2. Second send attempt on same digest -> strictly skipped due to idempotency!
    mock_provider.sent_messages.clear()
    result2 = await delivery_service.send_digest(digest_id=digest.id, dry_run=False)
    assert result2.sent_count == 0
    assert result2.skipped_count >= 2
    assert len(mock_provider.sent_messages) == 0  # Zero duplicate emails dispatched!


@pytest.mark.asyncio
async def test_delivery_provider_failure_resilience(db_session: AsyncSession):
    """Test that email provider failure is safely logged without breaking the database or pipeline."""
    subscriber_service = SubscriberService(session=db_session)
    digest_service = DigestService(session=db_session)

    sub_email = f"fail_{uuid.uuid4().hex[:8]}@example.com"
    await subscriber_service.subscribe(SubscribeRequest(email=sub_email))

    unique_date = date(2026, 7, 1) + timedelta(days=uuid.uuid4().int % 100)
    digest = await digest_service.generate_daily_digest(target_date=unique_date)

    # Use failing mock provider
    failing_provider = MockEmailProvider(should_succeed=False, failure_message="SMTP 550 Mailbox unavailable")
    delivery_service = DeliveryService(session=db_session, email_provider=failing_provider)

    result = await delivery_service.send_digest(digest_id=digest.id, dry_run=False)
    assert result.failed_count >= 1

    # Check delivery log records the error message cleanly
    digest_repo = DigestRepository(db_session)
    records = await digest_repo.list_deliveries_for_digest(digest.id)
    failed_record = next(r for r in records if r.recipient_email == sub_email.lower())
    assert failed_record.status == "FAILED"
    assert "SMTP 550 Mailbox unavailable" in failed_record.error_message


@pytest.mark.asyncio
async def test_digest_api_endpoints(async_client: AsyncClient):
    """Test FastAPI digest generation, preview, and delivery endpoints."""
    # 1. Generate digest
    gen_res = await async_client.post(
        "/api/v1/digests/generate",
        json={"target_date": "2026-09-12", "force": True},
    )
    assert gen_res.status_code == 200
    digest_data = gen_res.json()
    digest_id = digest_data["id"]
    assert digest_data["digest_date"] == "2026-09-12"
    assert "title" in digest_data

    # 2. Get latest digest
    latest_res = await async_client.get("/api/v1/digests/latest")
    assert latest_res.status_code == 200
    assert "id" in latest_res.json()

    # 3. Preview HTML
    preview_res = await async_client.get(f"/api/v1/digests/{digest_id}/preview")
    assert preview_res.status_code == 200
    assert "text/html" in preview_res.headers["content-type"]
    assert "AI NEWS INTELLIGENCE" in preview_res.text

    # 4. Dry run send
    send_res = await async_client.post(
        f"/api/v1/digests/{digest_id}/send",
        json={"dry_run": True},
    )
    assert send_res.status_code == 200
    send_data = send_res.json()
    assert send_data["dry_run"] is True
    assert send_data["digest_id"] == digest_id

    # 5. List deliveries
    deliv_res = await async_client.get(f"/api/v1/digests/{digest_id}/deliveries")
    assert deliv_res.status_code == 200
    assert isinstance(deliv_res.json(), list)
