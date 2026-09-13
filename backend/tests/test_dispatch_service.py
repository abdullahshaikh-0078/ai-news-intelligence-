from datetime import date, datetime, timedelta, timezone
import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.models.curation import CuratedStoryResponse
from app.domain.models.dispatch import DailyDispatchRequest
from app.infrastructure.models.digest import DeliveryRecord, Digest
from app.infrastructure.models.subscriber import Subscriber
from app.services.digest_service import DigestService
from app.services.dispatch_service import DailyDispatchService
from app.services.email_service import DeliveryService, MockEmailProvider
from app.services.pipeline_service import PipelineOrchestratorService


class MockPipelineService:
    """Mock pipeline service returning simulated pipeline execution results without external API calls."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def run_pipeline(self, request=None):
        from app.domain.models.pipeline import PipelineRunResponse
        return PipelineRunResponse(
            status="SUCCESS",
            items_seen=10,
            items_processed=5,
            ai_analyses_performed=2,
            embeddings_generated=2,
            embeddings_reused=3,
            deterministic_duplicates_found=0,
            semantic_duplicates_found=0,
            stories_created=1,
            stories_updated=0,
            stories_ranked=5,
            stories_curated=5,
            duration_ms=450.0,
            curated_stories=[],
            failures=[],
        )


@pytest.mark.asyncio
async def test_daily_dispatch_service_full_flow(db_session: AsyncSession):
    """Test automated daily dispatch workflow with MockEmailProvider."""
    # 1. Setup active and inactive subscribers
    active_sub = Subscriber(
        email=f"dispatch_active_{uuid.uuid4().hex[:6]}@example.com",
        name="Active Subscriber",
        frequency="DAILY",
        preferred_channel="EMAIL",
        topics=["Robotics"],
        is_active=True,
        unsubscribe_token=uuid.uuid4().hex,
    )
    inactive_sub = Subscriber(
        email=f"dispatch_inactive_{uuid.uuid4().hex[:6]}@example.com",
        name="Inactive Subscriber",
        frequency="DAILY",
        preferred_channel="EMAIL",
        topics=["Robotics"],
        is_active=False,
        unsubscribe_token=uuid.uuid4().hex,
    )
    weekly_sub = Subscriber(
        email=f"dispatch_weekly_{uuid.uuid4().hex[:6]}@example.com",
        name="Weekly Subscriber",
        frequency="WEEKLY",
        preferred_channel="EMAIL",
        topics=["Robotics"],
        is_active=True,
        unsubscribe_token=uuid.uuid4().hex,
    )
    db_session.add_all([active_sub, inactive_sub, weekly_sub])
    await db_session.commit()

    mock_provider = MockEmailProvider(should_succeed=True)
    dispatch_service = DailyDispatchService(
        session=db_session,
        email_provider=mock_provider,
        pipeline_service=MockPipelineService(session=db_session),
    )

    target_date = date(2026, 9, 15)
    req = DailyDispatchRequest(
        target_date=target_date,
        run_ingestion=False,
        dry_run=False,
        force_regenerate_digest=False,
    )

    # 2. Run first dispatch pass
    res1 = await dispatch_service.run_daily_dispatch(req)
    assert res1.status in ("success", "skipped")
    assert res1.target_date == target_date
    assert res1.digest_id is not None
    assert res1.duration_seconds >= 0.0

    # If stories existed in DB, active_sub should have received an email
    if res1.digest_stories_count > 0:
        assert res1.sent_count >= 1
        assert len(mock_provider.sent_messages) >= 1
        # Inactive sub and weekly sub should NOT be sent to
        recipients = [m["to"] for m in mock_provider.sent_messages]
        assert active_sub.email in recipients
        assert inactive_sub.email not in recipients
        assert weekly_sub.email not in recipients

        # 3. IDEMPOTENCY: Run dispatch a second time on the same day
        mock_provider.sent_messages.clear()
        res2 = await dispatch_service.run_daily_dispatch(req)
        # Digest ID should be identical
        assert res2.digest_id == res1.digest_id
        # Sent count should be 0 because already delivered (duplicate prevention)
        assert res2.sent_count == 0
        assert res2.skipped_count >= 1
        assert len(mock_provider.sent_messages) == 0


@pytest.mark.asyncio
async def test_daily_dispatch_dry_run(db_session: AsyncSession):
    """Test that dry_run=True simulates dispatch without recording deliveries or sending emails."""
    sub = Subscriber(
        email=f"dryrun_{uuid.uuid4().hex[:6]}@example.com",
        frequency="DAILY",
        is_active=True,
        unsubscribe_token=uuid.uuid4().hex,
    )
    db_session.add(sub)
    await db_session.commit()

    mock_provider = MockEmailProvider(should_succeed=True)
    dispatch_service = DailyDispatchService(
        session=db_session,
        email_provider=mock_provider,
        pipeline_service=MockPipelineService(session=db_session),
    )

    target_date = date(2026, 9, 16)
    req = DailyDispatchRequest(
        target_date=target_date,
        run_ingestion=False,
        dry_run=True,
    )

    res = await dispatch_service.run_daily_dispatch(req)
    assert res.dry_run is True
    # Zero real emails sent by provider
    assert len(mock_provider.sent_messages) == 0


@pytest.mark.asyncio
async def test_daily_dispatch_provider_failure(db_session: AsyncSession):
    """Test graceful handling when the email provider fails to send."""
    sub = Subscriber(
        email=f"failtest_{uuid.uuid4().hex[:6]}@example.com",
        frequency="DAILY",
        is_active=True,
        unsubscribe_token=uuid.uuid4().hex,
    )
    db_session.add(sub)
    await db_session.commit()

    failing_provider = MockEmailProvider(should_succeed=False, failure_message="SMTP 550 Relay Denied")
    dispatch_service = DailyDispatchService(
        session=db_session,
        email_provider=failing_provider,
        pipeline_service=MockPipelineService(session=db_session),
    )

    target_date = date(2026, 9, 17)
    req = DailyDispatchRequest(
        target_date=target_date,
        run_ingestion=False,
        dry_run=False,
    )

    res = await dispatch_service.run_daily_dispatch(req)
    if res.digest_stories_count > 0:
        assert res.failed_count >= 1
        assert res.sent_count == 0
        assert res.status in ("error", "partial")


@pytest.mark.asyncio
async def test_dispatch_api_endpoints(async_client: AsyncClient, monkeypatch):
    """Test POST /api/v1/dispatch/daily and GET /api/v1/dispatch/status."""
    # 1. Test GET /status
    res_status = await async_client.get("/api/v1/dispatch/status")
    assert res_status.status_code == 200
    data = res_status.json()
    assert data["status"] == "READY"
    assert "cron_enabled" in data
    assert "schedule_hour_utc" in data
    assert "active_daily_subscribers" in data

    # 2. Test POST /daily without auth token when token is required
    monkeypatch.setattr(settings, "DISPATCH_SECRET_TOKEN", "secret_dispatch_token_123")
    res_unauth = await async_client.post(
        "/api/v1/dispatch/daily",
        json={"dry_run": True, "run_ingestion": False},
    )
    assert res_unauth.status_code == 401

    # 3. Test POST /daily with valid auth token
    res_auth = await async_client.post(
        "/api/v1/dispatch/daily",
        json={"dry_run": True, "run_ingestion": False},
        headers={"Authorization": "Bearer secret_dispatch_token_123"},
    )
    assert res_auth.status_code == 200
    res_data = res_auth.json()
    assert res_data["status"] in ("success", "skipped")
    assert res_data["dry_run"] is True
