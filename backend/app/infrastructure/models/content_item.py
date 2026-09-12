import uuid
from datetime import datetime
from typing import Any, List, Optional
from sqlalchemy import DateTime, Dialect, Float, ForeignKey, Index, String, Text, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector import Vector as PgVector
from pgvector.sqlalchemy import Vector
from app.infrastructure.database.base import Base, TimestampMixin


class AdaptiveVector(Vector):
    """Adaptive Vector type supporting native pgvector extension and fallback array domains."""
    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "VECTOR"

    def bind_processor(self, dialect: Dialect) -> Any:
        def process(value: Any) -> Any:
            if value is None:
                return None
            if isinstance(value, str):
                return value
            return PgVector._to_db(value)
        return process

    def result_processor(self, dialect: Dialect, coltype: Any) -> Any:
        def process(value: Any) -> Any:
            if value is None:
                return None
            if isinstance(value, (list, tuple)):
                return [float(x) for x in value]
            return PgVector._from_db(value)
        return process


class ContentItem(Base, TimestampMixin):
    """Normalized ingested content item maintaining strict provenance to its origin source."""

    __tablename__ = "content_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    story_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stories.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    canonical_url: Mapped[str] = mapped_column(String(2048), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    author: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    external_id: Mapped[Optional[str]] = mapped_column(String(512), nullable=True, index=True)
    language: Mapped[Optional[str]] = mapped_column(String(10), default="en", server_default="en", nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    content_type: Mapped[str] = mapped_column(String(50), default="ARTICLE", server_default="ARTICLE", index=True, nullable=False)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    processing_state: Mapped[str] = mapped_column(String(50), default="PENDING", index=True, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # AI Intelligence & Embeddings (Phase 9)
    ai_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ai_key_points: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    ai_topics: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    ai_relevance_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True, index=True)
    ai_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    ai_processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    embedding_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    embedding: Mapped[Optional[list]] = mapped_column(AdaptiveVector(1536), nullable=True)

    # Relationships
    source: Mapped["Source"] = relationship(
        "Source",
        back_populates="items",
    )
    story: Mapped[Optional["Story"]] = relationship(
        "Story",
        back_populates="articles",
        foreign_keys=[story_id],
    )
    story_associations: Mapped[List["StoryContentItem"]] = relationship(
        "StoryContentItem",
        back_populates="content_item",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_content_items_source_published", "source_id", "published_at"),
        Index("ix_content_items_source_external", "source_id", "external_id"),
    )

    def __repr__(self) -> str:
        return f"<ContentItem(title='{self.title[:40]}...', state='{self.processing_state}')>"
