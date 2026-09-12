import hashlib
import math
from typing import Any, Dict, List, Optional

from app.ai.base import BaseAIProvider
from app.domain.models.content_item import AIAnalysisResult


class MockAIProvider(BaseAIProvider):
    """Deterministic, offline AI provider for unit and integration testing.
    
    Generates synthetic structured analysis and 1536-dimensional normalized embeddings
    without network calls, API keys, or external rate limits.
    """

    def __init__(
        self,
        chat_model: str = "mock-gpt-4o-mini",
        embedding_model: str = "mock-embedding-3-small",
        embedding_dimensions: int = 1536,
        simulate_error: Optional[Exception] = None,
    ):
        self._chat_model = chat_model
        self._embedding_model = embedding_model
        self._embedding_dimensions = embedding_dimensions
        self.simulate_error = simulate_error
        self.call_count_analyze = 0
        self.call_count_embedding = 0

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def model_name(self) -> str:
        return self._chat_model

    @property
    def embedding_model_name(self) -> str:
        return self._embedding_model

    async def analyze_content(
        self,
        text: str,
        content_type: str = "ARTICLE",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AIAnalysisResult:
        if self.simulate_error:
            raise self.simulate_error

        self.call_count_analyze += 1
        lowered = text.lower()

        # Deterministic topic identification based on content
        topics = []
        if any(k in lowered for k in ["llm", "reasoning", "gpt", "claude", "language model"]):
            topics.append("LLMs")
        if any(k in lowered for k in ["agent", "autonomous", "tool use"]):
            topics.append("AI Agents")
        if any(k in lowered for k in ["vision", "video", "multimodal", "image"]):
            topics.append("Multimodal AI")
        if any(k in lowered for k in ["paper", "arxiv", "theorem", "dataset", "benchmark"]):
            topics.append("Research")
        if any(k in lowered for k in ["open-source", "github", "huggingface", "weights"]):
            topics.append("Open Source")
        if any(k in lowered for k in ["infra", "gpu", "cuda", "cluster", "tpu"]):
            topics.append("AI Infrastructure")

        if not topics:
            topics = ["Artificial Intelligence", "Technology"]

        # Deterministic relevance assessment (0.0 to 1.0)
        relevance = 0.5
        if content_type == "RESEARCH_PAPER":
            relevance = 0.88
        elif content_type == "VIDEO":
            relevance = 0.82
        elif content_type == "COMMUNITY_POST":
            relevance = 0.78
        elif "ai" in lowered or "model" in lowered:
            relevance = 0.90

        summary = f"Synthesized summary for: {text[:80].strip()}... This covers advancements in {', '.join(topics)}."
        key_points = [
            f"Primary highlight regarding {topics[0] if topics else 'AI'}.",
            f"Key technical takeaway extracted from {content_type.lower()} content.",
            f"Factual implication for downstream developer ecosystem.",
        ]

        return AIAnalysisResult(
            summary=summary,
            key_points=key_points,
            topics=topics,
            relevance_score=relevance,
            model_name=self._chat_model,
        )

    async def generate_embedding(self, text: str) -> List[float]:
        if self.simulate_error:
            raise self.simulate_error

        self.call_count_embedding += 1

        # Generate a deterministic, unit-normalized vector of dimension self._embedding_dimensions
        # using SHA-256 hash seeds so identical text yields identical vectors
        h = hashlib.sha256(text.encode("utf-8")).digest()
        raw = []
        for i in range(self._embedding_dimensions):
            byte_val = h[i % len(h)]
            # Pseudo-random float between -1.0 and 1.0
            val = ((byte_val ^ (i * 37 & 0xFF)) / 128.0) - 1.0
            raw.append(val)

        # Normalize to unit length (L2 norm = 1.0)
        norm = math.sqrt(sum(x * x for x in raw))
        if norm == 0:
            return [0.0] * self._embedding_dimensions
        return [round(x / norm, 6) for x in raw]
