from abc import ABC, abstractmethod
from typing import List
from app.domain.models.source import SourceType
from app.domain.schemas.ingestion import NormalizedArticle
from app.infrastructure.models.source import Source


class BaseIngestionAdapter(ABC):
    """Abstract interface defining contracts for source ingestion adapters."""

    @property
    @abstractmethod
    def source_type(self) -> SourceType:
        """The source type handled by this adapter (e.g., RSS, ARXIV, YOUTUBE)."""
        pass

    @abstractmethod
    async def fetch_and_parse(self, source: Source) -> List[NormalizedArticle]:
        """Fetch raw feed/data from source endpoint, normalize into articles, and validate."""
        pass
