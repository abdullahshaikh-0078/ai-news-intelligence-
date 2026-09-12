from datetime import datetime
import uuid
from typing import Optional
from sqlalchemy import DateTime, Float, ForeignKey, Index, String, JSON, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.infrastructure.database.base import Base, TimestampMixin


class ContentDuplicatePair(Base, TimestampMixin):
    """Represents an identified duplicate or highly similar relationship between two ContentItems."""

    __tablename__ = "content_duplicate_pairs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    content_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content_items.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    duplicate_content_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content_items.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    similarity_score: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    cosine_distance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    classification: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    detection_method: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # Relationships
    content_item = relationship(
        "ContentItem",
        foreign_keys=[content_item_id],
        lazy="selectin",
    )
    duplicate_content_item = relationship(
        "ContentItem",
        foreign_keys=[duplicate_content_item_id],
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("content_item_id", "duplicate_content_item_id", name="uq_content_duplicate_pair"),
        Index("ix_content_duplicate_pairs_item_sim", "content_item_id", "similarity_score"),
    )

    def __repr__(self) -> str:
        return (
            f"<ContentDuplicatePair({self.content_item_id} -> "
            f"{self.duplicate_content_item_id}: {self.similarity_score:.4f} [{self.classification}])>"
        )
