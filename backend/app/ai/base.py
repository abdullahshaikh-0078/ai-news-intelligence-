from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from app.domain.models.content_item import AIAnalysisResult


class BaseAIProvider(ABC):
    """Abstract interface for AI intelligence and embedding providers.
    
    Decouples core business services from specific LLM vendors (OpenAI, Anthropic, Gemini, local models).
    """

    @abstractmethod
    async def analyze_content(
        self,
        text: str,
        content_type: str = "ARTICLE",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AIAnalysisResult:
        """Generate structured summary, key points, topics, and relevance assessment."""
        pass

    @abstractmethod
    async def generate_embedding(self, text: str) -> List[float]:
        """Generate a dense vector embedding for the given input text."""
        pass

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Vendor/provider identifier."""
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Primary chat/analysis model identifier."""
        pass

    @property
    @abstractmethod
    def embedding_model_name(self) -> str:
        """Embedding model identifier."""
        pass
