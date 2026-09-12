import uuid
from datetime import datetime
from typing import List, Optional
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.infrastructure.database.base import Base, TimestampMixin


class Story(Base, TimestampMixin):
    """Clustered story model representing a synthesized event."""

    __tablename__ = "stories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    headline: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    key_takeaway: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    why_it_matters: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    importance_score: Mapped[float] = mapped_column(Float, default=0.0, index=True, nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(100), index=True, nullable=True)
    is_curated: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True, nullable=True)

    # Phase 11 Lifecycle & Metadata
    status: Mapped[str] = mapped_column(String(50), default="ACTIVE", index=True, nullable=False)
    canonical_content_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # Phase 12 & 13 Ranking & Curation
    ranking_score: Mapped[float] = mapped_column(Float, default=0.0, index=True, nullable=False)
    ranking_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ranking_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    curated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    curation_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # Title property alias for headline
    @property
    def title(self) -> str:
        return self.headline

    @title.setter
    def title(self, val: str) -> None:
        self.headline = val

    # Relationships
    articles: Mapped[List["ContentItem"]] = relationship(
        "ContentItem",
        back_populates="story",
        foreign_keys="ContentItem.story_id",
    )
    story_items: Mapped[List["StoryContentItem"]] = relationship(
        "StoryContentItem",
        back_populates="story",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    canonical_content_item = relationship(
        "ContentItem",
        foreign_keys=[canonical_content_item_id],
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_stories_curated_importance", "is_curated", "importance_score"),
        Index("ix_stories_status", "status"),
    )

    def __repr__(self) -> str:
        return f"<Story(headline='{self.headline[:40]}...', status='{self.status}')>"
