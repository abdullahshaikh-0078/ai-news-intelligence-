import math
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from google.genai import errors as genai_errors

from app.ai.providers.gemini_provider import (
    AIAnalysisSchema,
    AIProviderConfigurationError,
    AIProviderError,
    AIProviderTransientError,
    GeminiProvider,
)
from app.ai.providers.mock_provider import MockAIProvider
from app.domain.models.content_item import AIAnalysisResult


@pytest.mark.asyncio
async def test_mock_ai_provider_analysis():
    """Verify MockAIProvider generates valid structured analysis conforming to domain schemas."""
    provider = MockAIProvider(chat_model="test-model")
    res = await provider.analyze_content(
        text="Google DeepMind announces breakthrough in reasoning scaling laws.",
        content_type="ARTICLE",
    )

    assert isinstance(res, AIAnalysisResult)
    assert len(res.summary) > 0
    assert len(res.key_points) >= 2
    assert "LLMs" in res.topics or "Research" in res.topics
    assert 0.0 <= res.relevance_score <= 1.0
    assert res.model_name == "test-model"
    assert provider.call_count_analyze == 1


@pytest.mark.asyncio
async def test_mock_ai_provider_embedding():
    """Verify MockAIProvider produces deterministic 1536-dimensional unit-normalized embeddings."""
    provider = MockAIProvider(embedding_dimensions=1536)
    text = "Scaling laws for neural language models."

    emb1 = await provider.generate_embedding(text)
    emb2 = await provider.generate_embedding(text)

    assert len(emb1) == 1536
    # Determinism: identical input yields identical vector
    assert emb1 == emb2

    # Unit normalization: sum of squares ≈ 1.0
    norm = math.sqrt(sum(x * x for x in emb1))
    assert abs(norm - 1.0) < 1e-4
    assert provider.call_count_embedding == 2


@pytest.mark.asyncio
async def test_mock_ai_provider_simulated_error():
    """Verify MockAIProvider correctly raises configured simulation errors."""
    provider = MockAIProvider(simulate_error=RuntimeError("Simulated provider outage"))

    with pytest.raises(RuntimeError, match="Simulated provider outage"):
        await provider.analyze_content("Sample text")

    with pytest.raises(RuntimeError, match="Simulated provider outage"):
        await provider.generate_embedding("Sample text")


@pytest.mark.asyncio
async def test_gemini_provider_missing_key_raises_configuration_error():
    """Verify GeminiProvider raises AIProviderConfigurationError when API key is missing."""
    with pytest.raises(AIProviderConfigurationError, match="Gemini API key is not configured"):
        GeminiProvider(api_key="")


@pytest.mark.asyncio
async def test_gemini_provider_successful_mocked_calls():
    """Verify GeminiProvider correctly invokes generate_content and embed_content."""
    provider = GeminiProvider(api_key="mock-gemini-key")

    mock_schema_obj = AIAnalysisSchema(
        summary="DeepMind announces Willow, a breakthrough quantum computing chip.",
        key_points=[
            "Willow demonstrates exponential error suppression below threshold.",
            "Benchmarked against state-of-the-art supercomputers.",
            "Paves path to fault-tolerant commercial quantum hardware.",
        ],
        topics=["Quantum Computing", "Research", "Hardware"],
        relevance_score=0.98,
    )

    mock_response = MagicMock()
    mock_response.parsed = mock_schema_obj

    mock_emb_val = MagicMock()
    mock_emb_val.values = [0.005] * 1536
    mock_emb_resp = MagicMock()
    mock_emb_resp.embeddings = [mock_emb_val]

    with patch.object(provider._client.models, "generate_content", return_value=mock_response), \
         patch.object(provider._client.models, "embed_content", return_value=mock_emb_resp):

        analysis_res = await provider.analyze_content("Quantum chip announcement text")
        assert analysis_res.summary.startswith("DeepMind announces Willow")
        assert len(analysis_res.key_points) == 3
        assert "Quantum Computing" in analysis_res.topics
        assert analysis_res.relevance_score == 0.98

        emb_res = await provider.generate_embedding("Quantum chip text")
        assert len(emb_res) == 1536
        assert emb_res[0] == 0.005


@pytest.mark.asyncio
async def test_gemini_provider_relevance_score_normalization():
    """Verify GeminiProvider normalizes scores > 1.0 (e.g. 0-10 or 0-100 scales)."""
    provider = GeminiProvider(api_key="mock-gemini-key")

    mock_response = MagicMock()
    mock_response.parsed = {
        "summary": "Notable open source model update.",
        "key_points": ["Point 1", "Point 2", "Point 3"],
        "topics": ["Open Source", "LLMs"],
        "relevance_score": 9.5,  # 10-point scale
    }

    with patch.object(provider._client.models, "generate_content", return_value=mock_response):
        res = await provider.analyze_content("Sample text")
        assert res.relevance_score == 0.95


@pytest.mark.asyncio
async def test_gemini_provider_transient_retry_and_exhaustion():
    """Verify GeminiProvider retries on transient errors and raises AIProviderTransientError on exhaustion."""
    provider = GeminiProvider(api_key="mock-gemini-key", max_retries=2)

    server_error = genai_errors.ServerError(
        code=503,
        response_json={"error": {"code": 503, "message": "High demand, try again later."}},
        response=MagicMock(status_code=503),
    )

    with patch.object(provider._client.models, "generate_content", side_effect=server_error), \
         patch("asyncio.sleep", AsyncMock()) as mock_sleep:

        with pytest.raises(AIProviderTransientError, match="Gemini service transient failure"):
            await provider.analyze_content("Prompt text")

        assert provider._client.models.generate_content.call_count == 2
        assert mock_sleep.call_count == 1


@pytest.mark.asyncio
async def test_gemini_provider_embedding_dimension_mismatch():
    """Verify GeminiProvider raises error if returned embedding dimensions do not match expected."""
    provider = GeminiProvider(api_key="mock-gemini-key", embedding_dimensions=1536)

    mock_emb_val = MagicMock()
    mock_emb_val.values = [0.01] * 768  # 768 instead of 1536
    mock_emb_resp = MagicMock()
    mock_emb_resp.embeddings = [mock_emb_val]

    with patch.object(provider._client.models, "embed_content", return_value=mock_emb_resp):
        with pytest.raises(AIProviderError, match="Embedding dimension mismatch: expected 1536, got 768"):
            await provider.generate_embedding("Sample text")


@pytest.mark.asyncio
async def test_gemini_provider_sanitizes_secrets_in_errors():
    """Verify GeminiProvider strips API key from error messages."""
    fake_key = "AIzaSySecretApiKey123456789"
    provider = GeminiProvider(api_key=fake_key)

    raw_error = Exception(f"Failed with key {fake_key} due to connection reset")

    with patch.object(provider._client.models, "generate_content", side_effect=raw_error):
        with pytest.raises(AIProviderError) as exc_info:
            await provider.analyze_content("Sample text")

        msg = str(exc_info.value)
        assert fake_key not in msg
        assert "[REDACTED_API_KEY]" in msg
