import uuid
from datetime import datetime
from typing import List, Optional
from sqlalchemy import Boolean, DateTime, JSON, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.infrastructure.database.base import Base, TimestampMixin


class Subscriber(Base, TimestampMixin):
    """Newsletter subscriber model for Phase 17."""

    __tablename__ = "subscribers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    age_group: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    frequency: Mapped[str] = mapped_column(String(50), default="DAILY", nullable=False)
    preferred_channel: Mapped[str] = mapped_column(String(50), default="EMAIL", nullable=False)
    topics: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True, nullable=False)
    unsubscribe_token: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    unsubscribed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    deliveries: Mapped[List["DeliveryRecord"]] = relationship(
        "DeliveryRecord",
        back_populates="subscriber",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Subscriber(email='{self.email}', is_active={self.is_active})>"
