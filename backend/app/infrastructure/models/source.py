import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, Integer, String, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.infrastructure.database.base import Base, TimestampMixin


class Source(Base, TimestampMixin):
    """Source registry model defining upstream content providers."""

    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint("reliability_score >= 0.0 AND reliability_score <= 1.0", name="ck_source_reliability_score"),
        CheckConstraint("fetch_interval_minutes >= 5", name="ck_source_fetch_interval_minutes"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    type: Mapped[str] = mapped_column(String(50), index=True, nullable=False)  # RSS, WEB, ARXIV, YOUTUBE, GITHUB
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True, nullable=False)
    language: Mapped[str] = mapped_column(String(10), default="en", server_default="en", nullable=False)
    reliability_score: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    fetch_interval_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    last_fetched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    config: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    @property
    def configuration(self) -> Dict[str, Any]:
        """Alias for config attribute to align with API and domain schemas."""
        return self.config

    @configuration.setter
    def configuration(self, val: Dict[str, Any]) -> None:
        self.config = val or {}

    # Relationships
    items: Mapped[List["ContentItem"]] = relationship(
        "ContentItem",
        back_populates="source",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Source(name='{self.name}', type='{self.type}', enabled={self.enabled})>"
