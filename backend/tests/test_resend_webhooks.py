import base64
from datetime import date, datetime, timezone
import hashlib
import hmac
import json
import time
import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.infrastructure.models.digest import DeliveryRecord, Digest
from app.infrastructure.models.subscriber import Subscriber
from app.services.webhook_service import ResendWebhookService, verify_svix_signature


def generate_svix_headers(secret: str, msg_id: str, body_bytes: bytes, timestamp_offset: int = 0) -> dict:
    """Helper creating valid Svix headers for testing."""
    ts = str(int(time.time()) + timestamp_offset)
    try:
        sec_clean = secret[6:] if secret.startswith("whsec_") else secret
        missing_padding = len(sec_clean) % 4
        if missing_padding:
            sec_clean += "=" * (4 - missing_padding)
        s_bytes = base64.b64decode(sec_clean)
    except Exception:
        s_bytes = secret.encode("utf-8")

    to_sign = f"{msg_id}.{ts}.{body_bytes.decode('utf-8')}".encode("utf-8")
    sig = base64.b64encode(hmac.new(s_bytes, to_sign, hashlib.sha256).digest()).decode("utf-8")
    return {
        "svix-id": msg_id,
        "svix-timestamp": ts,
        "svix-signature": f"v1,{sig}",
    }


def test_verify_svix_signature_algorithm():
    """Unit test the Svix HMAC-SHA256 signature verification protocol."""
    secret = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
    msg_id = "msg_0123456789abcdef"
    body = b'{"type":"email.delivered","data":{"email_id":"email_123"}}'

    # 1. Valid signature
    headers = generate_svix_headers(secret, msg_id, body)
    valid, msg = verify_svix_signature(
        secret=secret,
        svix_id=headers["svix-id"],
        svix_timestamp=headers["svix-timestamp"],
        body_bytes=body,
        signature_header=headers["svix-signature"],
    )
    assert valid is True
    assert msg == "Valid signature"

    # 2. Tampered body
    tampered_body = b'{"type":"email.bounced","data":{"email_id":"email_123"}}'
    valid, msg = verify_svix_signature(
        secret=secret,
        svix_id=headers["svix-id"],
        svix_timestamp=headers["svix-timestamp"],
        body_bytes=tampered_body,
        signature_header=headers["svix-signature"],
    )
    assert valid is False
    assert "mismatch" in msg.lower()

    # 3. Wrong secret
    valid, msg = verify_svix_signature(
        secret="whsec_WrongSecretKey123456789012345678",
        svix_id=headers["svix-id"],
        svix_timestamp=headers["svix-timestamp"],
        body_bytes=body,
        signature_header=headers["svix-signature"],
    )
    assert valid is False

    # 4. Expired timestamp (>300s in the past)
    expired_headers = generate_svix_headers(secret, msg_id, body, timestamp_offset=-400)
    valid, msg = verify_svix_signature(
        secret=secret,
        svix_id=expired_headers["svix-id"],
        svix_timestamp=expired_headers["svix-timestamp"],
        body_bytes=body,
        signature_header=expired_headers["svix-signature"],
        tolerance=300,
    )
    assert valid is False
    assert "tolerance" in msg.lower()

    # 5. Missing headers
    valid, msg = verify_svix_signature("", msg_id, "12345", body, "v1,abc")
    assert valid is False


@pytest.mark.asyncio
async def test_resend_webhook_service_lifecycle(db_session: AsyncSession):
    """Test full event handling lifecycle for Resend webhooks."""
    # 1. Setup mock subscriber, digest, and delivery record
    sub = Subscriber(
        email=f"webhook_{uuid.uuid4().hex[:6]}@example.com",
        name="Webhook Test User",
        frequency="DAILY",
        preferred_channel="EMAIL",
        topics=["Agents"],
        is_active=True,
        unsubscribe_token=uuid.uuid4().hex,
    )
    digest = Digest(
        title="Test Digest Issue",
        digest_date=date(2026, 9, 12),
        status="SENT",
        story_count=5,
        html_content="<p>Test</p>",
        plain_text_content="Test",
    )
    db_session.add_all([sub, digest])
    await db_session.flush()

    msg_id = f"re_msg_{uuid.uuid4().hex[:12]}"
    delivery = DeliveryRecord(
        digest_id=digest.id,
        subscriber_id=sub.id,
        channel="EMAIL",
        status="SENT",
        recipient_email=sub.email,
        provider="RESEND",
        provider_message_id=msg_id,
        attempted_at=datetime.now(timezone.utc),
    )
    db_session.add(delivery)
    await db_session.commit()

    service = ResendWebhookService(session=db_session)

    # 2. Test email.delivered event
    deliv_event = {
        "type": "email.delivered",
        "data": {
            "email_id": msg_id,
            "to": [sub.email],
            "subject": digest.title,
        },
    }
    res = await service.process_event(deliv_event)
    assert res.status == "processed"
    assert res.event_type == "email.delivered"
    assert res.message_id == msg_id

    # Verify database state
    await db_session.refresh(delivery)
    assert delivery.status == "DELIVERED"
    assert delivery.delivered_at is not None
    assert "webhook_delivered" in delivery.metadata_json

    # 3. Test email.opened event
    open_event = {
        "type": "email.opened",
        "data": {
            "email_id": msg_id,
            "to": [sub.email],
        },
    }
    res_open = await service.process_event(open_event)
    assert res_open.status == "processed"
    await db_session.refresh(delivery)
    assert delivery.status == "OPENED"
    assert "opened_at" in delivery.metadata_json

    # 4. Test email.clicked event
    click_event = {
        "type": "email.clicked",
        "data": {
            "email_id": msg_id,
            "click": {
                "link": "https://deepmind.google/blog/agents",
            },
        },
    }
    res_click = await service.process_event(click_event)
    assert res_click.status == "processed"
    await db_session.refresh(delivery)
    assert delivery.status == "CLICKED"
    assert delivery.metadata_json.get("clicked_link") == "https://deepmind.google/blog/agents"

    # 5. Test email.bounced (hard bounce) -> deactivates subscriber
    bounce_event = {
        "type": "email.bounced",
        "data": {
            "email_id": msg_id,
            "bounce": {
                "type": "hard",
                "message": "User unknown",
            },
        },
    }
    res_bounce = await service.process_event(bounce_event)
    assert res_bounce.status == "processed"
    assert res_bounce.subscriber_deactivated is True
    await db_session.refresh(delivery)
    await db_session.refresh(sub)
    assert delivery.status == "BOUNCED"
    assert sub.is_active is False

    # 6. Test email.complained -> deactivates subscriber
    sub2 = Subscriber(
        email=f"complain_{uuid.uuid4().hex[:6]}@example.com",
        frequency="DAILY",
        is_active=True,
        unsubscribe_token=uuid.uuid4().hex,
    )
    db_session.add(sub2)
    await db_session.flush()

    msg_id2 = f"re_msg2_{uuid.uuid4().hex[:12]}"
    delivery2 = DeliveryRecord(
        digest_id=digest.id,
        subscriber_id=sub2.id,
        channel="EMAIL",
        status="SENT",
        recipient_email=sub2.email,
        provider="RESEND",
        provider_message_id=msg_id2,
    )
    db_session.add(delivery2)
    await db_session.commit()

    complaint_event = {
        "type": "email.complained",
        "data": {
            "email_id": msg_id2,
        },
    }
    res_comp = await service.process_event(complaint_event)
    assert res_comp.status == "processed"
    assert res_comp.subscriber_deactivated is True
    await db_session.refresh(sub2)
    assert sub2.is_active is False


@pytest.mark.asyncio
async def test_resend_webhook_ignored_events(db_session: AsyncSession):
    """Test webhook resilience against non-existent delivery records or missing email_id."""
    service = ResendWebhookService(session=db_session)

    # Missing email_id
    res1 = await service.process_event({"type": "email.delivered", "data": {}})
    assert res1.status == "ignored"

    # Non-existent email_id
    res2 = await service.process_event({
        "type": "email.delivered",
        "data": {"email_id": "non_existent_msg_12345"},
    })
    assert res2.status == "ignored"
    assert "No matching DeliveryRecord" in res2.details


@pytest.mark.asyncio
async def test_resend_webhook_endpoint_security(async_client: AsyncClient, monkeypatch):
    """Test webhook signature validation and security on the POST route."""
    secret = "whsec_TestWebhookSecret1234567890123456"
    monkeypatch.setattr(settings, "RESEND_WEBHOOK_SECRET", secret)

    payload = {
        "type": "email.delivered",
        "data": {"email_id": "mock_id_999"},
    }
    body_bytes = json.dumps(payload).encode("utf-8")

    # 1. Missing headers -> 400 Bad Request
    res_no_headers = await async_client.post(
        "/api/v1/newsletter/webhooks/resend",
        content=body_bytes,
        headers={"Content-Type": "application/json"},
    )
    assert res_no_headers.status_code == 400
    assert "Missing required Svix signature headers" in res_no_headers.json()["detail"]

    # 2. Invalid signature -> 401 Unauthorized
    res_bad_sig = await async_client.post(
        "/api/v1/newsletter/webhooks/resend",
        content=body_bytes,
        headers={
            "Content-Type": "application/json",
            "svix-id": "msg_test",
            "svix-timestamp": str(int(time.time())),
            "svix-signature": "v1,invalidBase64Signature==",
        },
    )
    assert res_bad_sig.status_code == 401
    assert "Invalid webhook signature" in res_bad_sig.json()["detail"]

    # 3. Valid signature -> 200 OK (even if ignored for non-existent id)
    valid_headers = generate_svix_headers(secret, "msg_test", body_bytes)
    valid_headers["Content-Type"] = "application/json"
    res_valid = await async_client.post(
        "/api/v1/newsletter/webhooks/resend",
        content=body_bytes,
        headers=valid_headers,
    )
    assert res_valid.status_code == 200
    data = res_valid.json()
    assert data["status"] == "ignored"
