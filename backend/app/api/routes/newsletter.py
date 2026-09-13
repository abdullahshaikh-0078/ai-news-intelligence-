import json
from typing import Any, Dict, List, Optional, Union
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.domain.models.newsletter import (
    PreferencesUpdateRequest,
    SubscribeRequest,
    SubscribeResponse,
    SubscriberProfileResponse,
    UnsubscribeRequest,
    UnsubscribeResponse,
)
from app.domain.models.webhook import WebhookProcessResult
from app.infrastructure.database.session import get_db
from app.services.subscriber_service import SubscriberService
from app.services.webhook_service import ResendWebhookService, verify_svix_signature

router = APIRouter(prefix="/newsletter", tags=["Newsletter & Subscribers"])


@router.post(
    "/subscribe",
    response_model=SubscribeResponse,
    status_code=status.HTTP_200_OK,
    summary="Subscribe to AI News Daily Digest",
)
async def subscribe_to_newsletter(
    request: SubscribeRequest,
    session: AsyncSession = Depends(get_db),
) -> SubscribeResponse:
    """Subscribe a new user or update preferences for an existing subscriber idempotently."""
    service = SubscriberService(session=session)
    subscriber, is_created = await service.subscribe(request)
    
    msg = (
        "You're in. Your AI News digest will be delivered to your inbox."
        if is_created
        else "Welcome back! Your subscription preferences have been updated."
    )
    return SubscribeResponse(
        status="success",
        message=msg,
        email=subscriber.email,
        is_active=subscriber.is_active,
        created=is_created,
    )


@router.post(
    "/unsubscribe",
    response_model=UnsubscribeResponse,
    summary="Unsubscribe from newsletter via token or email",
)
async def unsubscribe_post(
    request: UnsubscribeRequest,
    session: AsyncSession = Depends(get_db),
) -> UnsubscribeResponse:
    """Unsubscribe a user from the newsletter."""
    if not request.token and not request.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either token or email must be provided to unsubscribe.",
        )
    service = SubscriberService(session=session)
    success = await service.unsubscribe(token=request.token, email=request.email)
    if not success:
        return UnsubscribeResponse(
            status="not_found",
            message="Subscriber record was not found or already unsubscribed.",
        )
    return UnsubscribeResponse(
        status="success",
        message="You have been successfully unsubscribed from AI News Daily Digest.",
    )


@router.get(
    "/unsubscribe",
    response_class=HTMLResponse,
    summary="One-click unsubscribe endpoint for email links",
)
async def unsubscribe_get(
    token: Optional[str] = Query(None, description="Subscriber unsubscribe token"),
    email: Optional[str] = Query(None, description="Subscriber email address"),
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Handle one-click unsubscribe links from delivered emails."""
    service = SubscriberService(session=session)
    success = await service.unsubscribe(token=token, email=email)
    
    status_text = (
        "You have been successfully unsubscribed."
        if success
        else "Subscription not found or already inactive."
    )
    
    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Unsubscribed — AI News</title>
  <style>
    body {{ background: #0b0f19; color: #f8fafc; font-family: -apple-system, sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
    .card {{ background: #0f172a; border: 1px solid #1e293b; padding: 40px; border-radius: 12px; max-width: 480px; text-align: center; }}
    h1 {{ font-size: 22px; margin-bottom: 12px; color: #38bdf8; }}
    p {{ color: #94a3b8; font-size: 14px; line-height: 1.6; }}
    a {{ color: #38bdf8; text-decoration: none; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>AI News Daily Digest</h1>
    <p>{status_text}</p>
    <p>We're sorry to see you go. You can re-subscribe at any time from our homepage.</p>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)


@router.put(
    "/preferences",
    response_model=SubscriberProfileResponse,
    summary="Update subscriber preferences",
)
async def update_preferences(
    request: PreferencesUpdateRequest,
    session: AsyncSession = Depends(get_db),
) -> SubscriberProfileResponse:
    """Update topic, frequency, and delivery preferences for an existing subscriber."""
    service = SubscriberService(session=session)
    updated = await service.update_preferences(request)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscriber not found. Please subscribe first.",
        )
    return SubscriberProfileResponse.model_validate(updated)


@router.get(
    "/subscribers/count",
    summary="Get active subscriber count",
)
async def get_subscriber_count(
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Return active subscriber count."""
    service = SubscriberService(session=session)
    count = await service.get_active_count()
    return {"active_subscribers": count}


@router.post(
    "/webhooks/resend",
    response_model=Union[WebhookProcessResult, List[WebhookProcessResult]],
    summary="Handle incoming Resend delivery webhooks",
)
async def handle_resend_webhook(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> Any:
    """
    Process incoming delivery webhooks from Resend (via Svix protocol).
    Verifies cryptographic signatures against RESEND_WEBHOOK_SECRET,
    mitigates replay attacks, and idempotently updates delivery_records.
    """
    raw_body = await request.body()
    svix_id = request.headers.get("svix-id")
    svix_timestamp = request.headers.get("svix-timestamp")
    svix_signature = request.headers.get("svix-signature")

    # If webhook secret is configured, strictly enforce signature verification
    if settings.RESEND_WEBHOOK_SECRET:
        if not svix_id or not svix_timestamp or not svix_signature:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing required Svix signature headers (svix-id, svix-timestamp, svix-signature)",
            )
        is_valid, msg = verify_svix_signature(
            secret=settings.RESEND_WEBHOOK_SECRET,
            svix_id=svix_id,
            svix_timestamp=svix_timestamp,
            body_bytes=raw_body,
            signature_header=svix_signature,
        )
        if not is_valid:
            logger.warning(f"Rejected unauthorized Resend webhook: {msg}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid webhook signature: {msg}",
            )
    else:
        logger.info("RESEND_WEBHOOK_SECRET not set; proceeding in unverified mode.")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSON payload: {exc}",
        )

    service = ResendWebhookService(session=session)
    if isinstance(payload, list):
        results = [await service.process_event(item) for item in payload]
        return results
    elif isinstance(payload, dict):
        result = await service.process_event(payload)
        return result
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload must be a JSON object or array of objects",
        )

