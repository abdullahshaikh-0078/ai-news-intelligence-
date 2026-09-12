from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field


class ResendWebhookEvent(BaseModel):
    """Payload representing an inbound Resend delivery event."""
    model_config = ConfigDict(extra="ignore")

    type: str = Field(..., description="Event type, e.g. email.delivered, email.bounced, email.complained, email.opened, email.clicked")
    created_at: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict, description="Event data containing email_id, recipient, bounce/click info")


class WebhookProcessResult(BaseModel):
    """Result of processing an inbound webhook event."""
    status: str = Field(..., description="'processed', 'ignored', or 'error'")
    event_type: str
    message_id: Optional[str] = None
    delivery_record_id: Optional[uuid.UUID] = None
    recipient_email: Optional[str] = None
    subscriber_deactivated: bool = False
    details: str
