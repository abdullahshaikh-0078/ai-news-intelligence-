import uuid
from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, JSON, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.infrastructure.database.base import Base, TimestampMixin


class StoryContentItem(Base, TimestampMixin):
    """Normalized association linking a ContentItem to a parent Story with provenance metadata."""

    __tablename__ = "story_content_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    story_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stories.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    content_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content_items.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    is_canonical: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # Relationships
    story = relationship(
        "Story",
        back_populates="story_items",
        foreign_keys=[story_id],
    )
    content_item = relationship(
        "ContentItem",
        back_populates="story_associations",
        foreign_keys=[content_item_id],
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("story_id", "content_item_id", name="uq_story_content_item"),
        Index("ix_story_content_items_story_canonical", "story_id", "is_canonical"),
    )

    def __repr__(self) -> str:
        return f"<StoryContentItem(story={self.story_id}, item={self.content_item_id}, canonical={self.is_canonical})>"
