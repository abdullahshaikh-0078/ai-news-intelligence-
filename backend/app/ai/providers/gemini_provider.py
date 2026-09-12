import asyncio
import json
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from google import genai
from google.genai import types
from google.genai import errors as genai_errors

from app.ai.base import BaseAIProvider
from app.core.config import settings
from app.core.logging import logger
from app.domain.models.content_item import AIAnalysisResult


class AIProviderError(Exception):
    """Base exception for AI provider operations."""
    pass


class AIProviderConfigurationError(AIProviderError):
    """Raised when provider configuration or credentials are missing or invalid."""
    pass


class AIProviderTransientError(AIProviderError):
    """Raised on recoverable transient errors (rate limit, service temporarily unavailable)."""
    pass


class AIAnalysisSchema(BaseModel):
    """Pydantic schema for structured output generation from Gemini."""
    summary: str = Field(description="Factual, concise summary of 2-3 sentences.")
    key_points: List[str] = Field(description="3 to 5 concise technical takeaways.")
    topics: List[str] = Field(description="Standardized AI/ML categories.")
    relevance_score: float = Field(description="Significance score for AI practitioners from 0.0 (irrelevant) to 1.0 (breakthrough).")


class GeminiProvider(BaseAIProvider):
    """Production AI intelligence provider powered by Google Gemini API and SDK."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        chat_model: Optional[str] = None,
        embedding_model: Optional[str] = None,
        embedding_dimensions: Optional[int] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ):
        self._api_key = settings.GEMINI_API_KEY if api_key is None else api_key
        if not self._api_key or not self._api_key.strip():
            raise AIProviderConfigurationError("Gemini API key is not configured. Set GEMINI_API_KEY in .env.")

        self._chat_model = chat_model or settings.GEMINI_CHAT_MODEL
        self._embedding_model = embedding_model or settings.GEMINI_EMBEDDING_MODEL
        self._embedding_dimensions = embedding_dimensions or settings.GEMINI_EMBEDDING_DIMENSIONS
        self._timeout = timeout or settings.AI_PROCESSING_TIMEOUT
        self._max_retries = max_retries or settings.AI_PROCESSING_MAX_RETRIES

        # Initialize official Google GenAI Client
        self._client = genai.Client(api_key=self._api_key)

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def model_name(self) -> str:
        return self._chat_model

    @property
    def embedding_model_name(self) -> str:
        return self._embedding_model

    def _sanitize_error_message(self, message: str) -> str:
        """Strip sensitive credentials from error logs and exception messages."""
        if self._api_key:
            return message.replace(self._api_key, "[REDACTED_API_KEY]")
        return message

    def _build_analysis_prompt(self, text: str, content_type: str, metadata: Optional[Dict[str, Any]]) -> str:
        """Build structured system prompt instructing Gemini to analyze the item."""
        return (
            "You are an expert AI/ML research analyst for an AI News Intelligence Platform.\n"
            "Analyze the following content item and return a structured JSON response conforming strictly to the requested schema.\n\n"
            "Requirements:\n"
            "- summary: Exactly 2 to 3 factual, information-dense sentences.\n"
            "- key_points: 3 to 5 concise technical takeaways.\n"
            "- topics: 2 to 6 standardized AI/ML categories (e.g., 'LLMs', 'AI Agents', 'Multimodal AI', 'Robotics', "
            "'AI Infrastructure', 'Research', 'Open Source', 'Computer Vision', 'Reinforcement Learning', 'AI Safety').\n"
            "- relevance_score: Float between 0.0 and 1.0 (1.0 = breakthrough research or major official model release; "
            "0.5 = notable industry update; 0.1 = peripheral or low-signal mention).\n\n"
            f"Content Type: {content_type}\n\n"
            f"Content Body:\n{text}"
        )

    async def analyze_content(
        self,
        text: str,
        content_type: str = "ARTICLE",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AIAnalysisResult:
        """Generate structured summary, key points, topics, and relevance assessment using Gemini."""
        prompt = self._build_analysis_prompt(text, content_type, metadata)
        last_exception: Optional[Exception] = None

        for attempt in range(self._max_retries):
            try:
                # Async execution via asyncio.to_thread to maintain asynchronous non-blocking event loop
                response = await asyncio.to_thread(
                    self._client.models.generate_content,
                    model=self._chat_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=AIAnalysisSchema,
                        temperature=0.2,
                    ),
                )

                # Parse and validate response
                if hasattr(response, "parsed") and response.parsed is not None:
                    parsed_data = response.parsed
                    if isinstance(parsed_data, AIAnalysisSchema):
                        raw_dict = parsed_data.model_dump()
                    elif isinstance(parsed_data, dict):
                        raw_dict = parsed_data
                    else:
                        raw_dict = json.loads(response.text)
                else:
                    raw_dict = json.loads(response.text)

                # Normalize relevance score to [0.0, 1.0] if model scaled to 10 or 100
                rel_score = float(raw_dict.get("relevance_score", 0.5))
                if rel_score > 1.0:
                    if rel_score <= 10.0:
                        rel_score = rel_score / 10.0
                    else:
                        rel_score = min(1.0, rel_score / 100.0)
                rel_score = max(0.0, min(1.0, rel_score))

                # Normalize key points and topics
                key_pts = [str(kp).strip() for kp in raw_dict.get("key_points", []) if str(kp).strip()]
                topics = [str(tp).strip() for tp in raw_dict.get("topics", []) if str(tp).strip()]
                summary = str(raw_dict.get("summary", "")).strip()

                return AIAnalysisResult(
                    summary=summary or "Summary unavailable.",
                    key_points=key_pts or ["Key points unavailable."],
                    topics=topics or ["Artificial Intelligence"],
                    relevance_score=rel_score,
                    model_name=self._chat_model,
                )

            except (genai_errors.ServerError, genai_errors.ClientError) as exc:
                sanitized_msg = self._sanitize_error_message(str(exc))
                status_code = getattr(exc, "code", getattr(exc, "status_code", None))
                
                # Check for transient errors: 429 (rate limit), 503 (unavailable), 500, 502, 504
                is_transient = status_code in (429, 500, 502, 503, 504) or "temporarily" in sanitized_msg.lower() or "demand" in sanitized_msg.lower()
                
                if is_transient and attempt < self._max_retries - 1:
                    backoff = (2 ** attempt) * 1.0
                    logger.warning(
                        f"[GeminiProvider] Transient error on attempt {attempt + 1}/{self._max_retries}: "
                        f"{sanitized_msg}. Retrying in {backoff:.1f}s..."
                    )
                    await asyncio.sleep(backoff)
                    last_exception = AIProviderTransientError(sanitized_msg)
                    continue

                if is_transient:
                    raise AIProviderTransientError(f"Gemini service transient failure after {self._max_retries} retries: {sanitized_msg}") from exc
                raise AIProviderError(f"Gemini service error: {sanitized_msg}") from exc

            except Exception as exc:
                sanitized_msg = self._sanitize_error_message(str(exc))
                if attempt < self._max_retries - 1 and ("timeout" in sanitized_msg.lower() or "connection" in sanitized_msg.lower()):
                    backoff = (2 ** attempt) * 1.0
                    await asyncio.sleep(backoff)
                    last_exception = AIProviderTransientError(sanitized_msg)
                    continue
                raise AIProviderError(f"Gemini analysis unexpected failure: {sanitized_msg}") from exc

        raise AIProviderTransientError(f"Gemini analysis exhausted retries: {last_exception}")

    async def generate_embedding(self, text: str) -> List[float]:
        """Generate a dense vector embedding using Gemini embedding model with target dimensions."""
        if not text or not text.strip():
            # Return zero vector if input is empty
            return [0.0] * self._embedding_dimensions

        for attempt in range(self._max_retries):
            try:
                response = await asyncio.to_thread(
                    self._client.models.embed_content,
                    model=self._embedding_model,
                    contents=text,
                    config=types.EmbedContentConfig(output_dimensionality=self._embedding_dimensions),
                )

                if not response.embeddings or not response.embeddings[0].values:
                    raise AIProviderError("Gemini embedding response contained no values.")

                vector = [float(v) for v in response.embeddings[0].values]

                if len(vector) != self._embedding_dimensions:
                    raise AIProviderError(
                        f"Embedding dimension mismatch: expected {self._embedding_dimensions}, got {len(vector)}."
                    )

                return vector

            except (genai_errors.ServerError, genai_errors.ClientError) as exc:
                sanitized_msg = self._sanitize_error_message(str(exc))
                status_code = getattr(exc, "code", getattr(exc, "status_code", None))
                is_transient = status_code in (429, 500, 502, 503, 504) or "demand" in sanitized_msg.lower()

                if is_transient and attempt < self._max_retries - 1:
                    backoff = (2 ** attempt) * 1.0
                    logger.warning(
                        f"[GeminiProvider] Embedding transient error on attempt {attempt + 1}/{self._max_retries}: "
                        f"{sanitized_msg}. Retrying in {backoff:.1f}s..."
                    )
                    await asyncio.sleep(backoff)
                    continue

                if is_transient:
                    raise AIProviderTransientError(f"Gemini embedding transient failure: {sanitized_msg}") from exc
                raise AIProviderError(f"Gemini embedding error: {sanitized_msg}") from exc

            except Exception as exc:
                sanitized_msg = self._sanitize_error_message(str(exc))
                if attempt < self._max_retries - 1 and ("timeout" in sanitized_msg.lower() or "connection" in sanitized_msg.lower()):
                    await asyncio.sleep((2 ** attempt) * 1.0)
                    continue
                raise AIProviderError(f"Gemini embedding unexpected failure: {sanitized_msg}") from exc

        raise AIProviderTransientError(f"Gemini embedding exhausted {self._max_retries} retries.")
