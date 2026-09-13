import base64
from datetime import datetime, timezone
import hashlib
import hmac
import time
from typing import Any, Dict, Optional, Tuple
import uuid
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.domain.models.webhook import WebhookProcessResult
from app.infrastructure.repositories.digest_repo import DigestRepository


def verify_svix_signature(
    secret: str,
    svix_id: str,
    svix_timestamp: str,
    body_bytes: bytes,
    signature_header: str,
    tolerance: int = 300,
) -> Tuple[bool, str]:
    """
    Verify Svix HMAC-SHA256 signature according to the standard webhook protocol.
    
    Args:
        secret: Secret key provided by provider (starts with 'whsec_' or raw base64).
        svix_id: Unique message ID from 'svix-id' header.
        svix_timestamp: Unix timestamp seconds from 'svix-timestamp' header.
        body_bytes: Raw binary HTTP request body.
        signature_header: Content of 'svix-signature' header.
        tolerance: Maximum clock skew in seconds (default 300s = 5min).
    """
    if not secret or not svix_id or not svix_timestamp or not signature_header:
        return False, "Missing required signature parameters or secret"

    # Verify timestamp freshness to mitigate replay attacks
    try:
        ts = int(svix_timestamp)
    except (ValueError, TypeError):
        return False, "Invalid svix-timestamp header"

    now = int(time.time())
    if abs(now - ts) > tolerance:
        return False, f"Webhook timestamp outside tolerance ({abs(now - ts)}s > {tolerance}s)"

    # Extract base64 secret bytes
    try:
        sec_clean = secret[6:] if secret.startswith("whsec_") else secret
        missing_padding = len(sec_clean) % 4
        if missing_padding:
            sec_clean += "=" * (4 - missing_padding)
        secret_bytes = base64.b64decode(sec_clean)
    except Exception:
        # Fallback to UTF-8 bytes if not standard base64
        secret_bytes = secret.encode("utf-8")

    # Construct signed payload
    try:
        body_str = body_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return False, "Request body is not valid UTF-8"

    to_sign = f"{svix_id}.{svix_timestamp}.{body_str}".encode("utf-8")
    computed_sig = base64.b64encode(
        hmac.new(secret_bytes, to_sign, hashlib.sha256).digest()
    ).decode("utf-8")

    # Check if computed signature matches any v1 signature part
    is_valid = False
    for sig_entry in signature_header.split(" "):
        parts = sig_entry.split(",", 1)
        if len(parts) == 2 and parts[0] == "v1":
            if hmac.compare_digest(parts[1], computed_sig):
                is_valid = True
                break

    if is_valid:
        return True, "Valid signature"
    return False, "Signature mismatch"


class ResendWebhookService:
    """Service handling inbound Resend delivery webhooks with signature verification and idempotency."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.digest_repo = DigestRepository(session)

    async def process_event(
        self,
        event_dict: Dict[str, Any],
    ) -> WebhookProcessResult:
        """
        Process a single Resend webhook event idempotently.
        Supported events:
          - email.delivered: Mark delivery_record as DELIVERED
          - email.bounced: Mark as BOUNCED, deactivate subscriber if hard bounce
          - email.complained: Mark as COMPLAINED, deactivate subscriber
          - email.opened: Track opened_at timestamp & status
          - email.clicked: Track clicked_at timestamp, link, & status
        """
        event_type = event_dict.get("type", "unknown")
        data = event_dict.get("data", {})
        message_id = data.get("email_id")

        if not message_id:
            logger.warning(f"Resend webhook event {event_type} missing data.email_id. Ignored.")
            return WebhookProcessResult(
                status="ignored",
                event_type=event_type,
                details="Event data does not contain 'email_id'",
            )

        # Lookup verified delivery record by external provider message ID
        record = await self.digest_repo.get_delivery_record_by_provider_message_id(message_id)
        if not record:
            logger.info(f"No delivery record matched provider_message_id='{message_id}' (event={event_type}). Ignored.")
            return WebhookProcessResult(
                status="ignored",
                event_type=event_type,
                message_id=message_id,
                details=f"No matching DeliveryRecord for message_id '{message_id}'",
            )

        subscriber_deactivated = False
        meta = dict(record.metadata_json) if record.metadata_json else {}
        now = datetime.now(timezone.utc)

        if event_type == "email.delivered":
            record.status = "DELIVERED"
            record.delivered_at = now
            meta["delivered_at"] = now.isoformat()
            meta["webhook_delivered"] = event_dict

        elif event_type == "email.bounced":
            record.status = "BOUNCED"
            bounce_data = data.get("bounce", {})
            meta["bounced_at"] = now.isoformat()
            meta["bounce_type"] = bounce_data.get("type", "unknown")
            meta["bounce_message"] = bounce_data.get("message")
            meta["webhook_bounced"] = event_dict

            # Flag subscriber inactive on hard bounce or default bounce
            bounce_type = str(bounce_data.get("type", "hard")).lower()
            if bounce_type in ("hard", "unknown", "permanent") and record.subscriber:
                if record.subscriber.is_active:
                    record.subscriber.is_active = False
                    subscriber_deactivated = True
                    self.session.add(record.subscriber)
                    logger.warning(f"Deactivated subscriber {record.subscriber.email} due to hard bounce.")

        elif event_type == "email.complained":
            record.status = "COMPLAINED"
            meta["complained_at"] = now.isoformat()
            meta["webhook_complained"] = event_dict

            # Deactivate subscriber immediately on spam complaint
            if record.subscriber and record.subscriber.is_active:
                record.subscriber.is_active = False
                subscriber_deactivated = True
                self.session.add(record.subscriber)
                logger.warning(f"Deactivated subscriber {record.subscriber.email} due to spam complaint.")

        elif event_type == "email.opened":
            if record.status not in ("BOUNCED", "COMPLAINED"):
                record.status = "OPENED"
            meta["opened_at"] = now.isoformat()
            meta["webhook_opened"] = event_dict

        elif event_type == "email.clicked":
            if record.status not in ("BOUNCED", "COMPLAINED"):
                record.status = "CLICKED"
            meta["clicked_at"] = now.isoformat()
            click_data = data.get("click", {})
            if click_data:
                meta["clicked_link"] = click_data.get("link")
            meta["webhook_clicked"] = event_dict

        else:
            meta[f"webhook_{event_type.replace('.', '_')}"] = event_dict

        record.metadata_json = meta
        self.session.add(record)
        await self.session.commit()

        logger.info(
            f"Processed Resend webhook: type='{event_type}', message_id='{message_id}', "
            f"record_id='{record.id}', recipient='{record.recipient_email}'"
        )
        return WebhookProcessResult(
            status="processed",
            event_type=event_type,
            message_id=message_id,
            delivery_record_id=record.id,
            recipient_email=record.recipient_email,
            subscriber_deactivated=subscriber_deactivated,
            details=f"Updated status to {record.status}",
        )
