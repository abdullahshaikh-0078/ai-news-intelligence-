import uuid
from datetime import date, datetime
from typing import List, Optional
from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.infrastructure.database.base import Base, TimestampMixin


class Digest(Base, TimestampMixin):
    """Daily Digest model representing a compiled newsletter issue."""

    __tablename__ = "digests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    digest_date: Mapped[date] = mapped_column(Date, unique=True, index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="DRAFT", index=True, nullable=False)
    story_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    html_content: Mapped[str] = mapped_column(Text, nullable=False)
    plain_text_content: Mapped[str] = mapped_column(Text, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    digest_stories: Mapped[List["DigestStory"]] = relationship(
        "DigestStory",
        back_populates="digest",
        cascade="all, delete-orphan",
        order_by="DigestStory.position",
        lazy="selectin",
    )
    deliveries: Mapped[List["DeliveryRecord"]] = relationship(
        "DeliveryRecord",
        back_populates="digest",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Digest(date={self.digest_date}, title='{self.title[:30]}...', stories={self.story_count})>"


class DigestStory(Base):
    """Junction table linking selected stories into an ordered digest issue."""

    __tablename__ = "digest_stories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    digest_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("digests.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    story_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stories.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    ranking_score_snapshot: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    headline_override: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    summary_override: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    why_it_matters_override: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    digest: Mapped["Digest"] = relationship("Digest", back_populates="digest_stories")
    story = relationship("Story", lazy="selectin")

    def __repr__(self) -> str:
        return f"<DigestStory(digest_id={self.digest_id}, story_id={self.story_id}, pos={self.position})>"


class DeliveryRecord(Base, TimestampMixin):
    """Audit log tracking delivery attempts and statuses for newsletter subscribers."""

    __tablename__ = "delivery_records"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    digest_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("digests.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    subscriber_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subscribers.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(String(50), default="EMAIL", nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="PENDING", index=True, nullable=False)
    recipient_email: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), default="RESEND", nullable=False)
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    attempted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # Relationships
    digest: Mapped["Digest"] = relationship("Digest", back_populates="deliveries")
    subscriber: Mapped["Subscriber"] = relationship("Subscriber", back_populates="deliveries")

    __table_args__ = (
        UniqueConstraint("digest_id", "subscriber_id", name="uq_delivery_records_digest_subscriber"),
    )

    def __repr__(self) -> str:
        return f"<DeliveryRecord(digest_id={self.digest_id}, recipient='{self.recipient_email}', status='{self.status}')>"
