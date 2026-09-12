import asyncio
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import BaseAIProvider
from app.ai.providers.gemini_provider import GeminiProvider
from app.ai.providers.mock_provider import MockAIProvider
from app.core.config import settings
from app.core.logging import logger
from app.domain.models.content_item import ProcessingStatus
from app.domain.schemas.ai import AIBatchProcessResponse, AIProcessResponse
from app.infrastructure.models.content_item import ContentItem
from app.infrastructure.repositories.content_repo import ContentRepository


class AIProcessingService:
    """Canonical service coordinating AI intelligence extraction, embeddings, and persistence.
    
    Operates uniformly on canonical ContentItem entities without source-specific branching.
    """

    def __init__(
        self,
        session: AsyncSession,
        provider: Optional[BaseAIProvider] = None,
        concurrency: Optional[int] = None,
    ):
        self.session = session
        self.content_repo = ContentRepository(session)
        self.concurrency = concurrency or settings.AI_PROCESSING_CONCURRENCY
        self._db_lock = asyncio.Lock()

        # Choose provider: explicit > Gemini (if key configured) > Mock
        if provider:
            self.provider = provider
        elif settings.GEMINI_API_KEY and settings.GEMINI_API_KEY.strip():
            self.provider = GeminiProvider()
        else:
            self.provider = MockAIProvider()

    def compile_processing_text(self, item: ContentItem, max_chars: int = 3000) -> str:
        """Compile canonical content into bounded text for LLM analysis.
        
        Applies sensible length limits to guarantee token/cost control.
        """
        parts = [f"Title: {item.title}"]

        if item.author:
            parts.append(f"Author/Source: {item.author}")

        if item.summary:
            parts.append(f"Summary/Abstract: {item.summary.strip()}")

        # Include bounded raw content if available and distinct from summary
        if item.raw_content and (not item.summary or item.raw_content.strip() != item.summary.strip()):
            content_snippet = item.raw_content.strip()[:max_chars]
            parts.append(f"Content Excerpt:\n{content_snippet}")

        # Canonical context from metadata if useful (e.g. categories, community score)
        if item.metadata_json:
            cats = item.metadata_json.get("categories")
            if cats and isinstance(cats, list):
                parts.append(f"Categories: {', '.join(str(c) for c in cats[:5])}")
            score = item.metadata_json.get("score")
            comments = item.metadata_json.get("comments")
            if score is not None:
                parts.append(f"Community Engagement: {score} points, {comments or 0} comments")

        return "\n\n".join(parts)

    def compile_embedding_text(self, item: ContentItem, max_chars: int = 1000) -> str:
        """Compile deterministic text representation for vector embedding generation."""
        title = item.title.strip()
        summary = (item.summary or item.raw_content or "").strip()[:max_chars]
        if summary:
            return f"{title}\n{summary}"
        return title

    async def analyze_item(self, item: ContentItem, force: bool = False) -> ContentItem:
        """Analyze a single ContentItem via LLM intelligence extraction without generating embeddings.
        
        Enforces idempotency: skips already analyzed items unless force=True.
        Guarantees fault isolation: preserves existing original data on failure.
        """
        # 1. Idempotency check: skip if already analyzed
        if item.ai_summary and item.processing_state == ProcessingStatus.COMPLETED.value and not force:
            logger.info(f"Skipping already analyzed ContentItem {item.id} ('{item.title[:40]}')")
            return item

        async with self._db_lock:
            item.processing_state = ProcessingStatus.PROCESSING.value
            await self.content_repo.update(item)

        try:
            prompt_text = self.compile_processing_text(item)
            analysis = await self.provider.analyze_content(
                text=prompt_text,
                content_type=item.content_type,
                metadata=item.metadata_json,
            )

            async with self._db_lock:
                item.ai_summary = analysis.summary
                item.ai_key_points = analysis.key_points
                item.ai_topics = analysis.topics
                item.ai_relevance_score = analysis.relevance_score
                item.ai_model = analysis.model_name or self.provider.model_name
                item.ai_processed_at = datetime.now(timezone.utc)
                item.processing_state = ProcessingStatus.COMPLETED.value
                if item.metadata_json and "processing_error" in item.metadata_json:
                    meta = dict(item.metadata_json)
                    meta.pop("processing_error", None)
                    item.metadata_json = meta
                await self.content_repo.update(item)

            logger.info(
                f"Successfully AI-analyzed ContentItem {item.id} "
                f"(relevance: {analysis.relevance_score:.2f}, topics: {len(analysis.topics)})"
            )
            return item

        except Exception as exc:
            async with self._db_lock:
                item.processing_state = ProcessingStatus.FAILED.value
                meta = dict(item.metadata_json or {})
                meta["processing_error"] = f"{exc.__class__.__name__}: {str(exc)[:200]}"
                item.metadata_json = meta
                try:
                    await self.content_repo.update(item)
                except Exception:
                    pass
            logger.error(
                f"AI analysis failed for ContentItem {item.id} ('{item.title[:40]}'): {exc.__class__.__name__}: {str(exc)}"
            )
            raise

    async def ensure_embedding(self, item: ContentItem, force: bool = False) -> tuple[Optional[List[float]], bool]:
        """Ensure an authentic embedding vector exists for the ContentItem on demand.
        
        Policy:
        - If embedding already exists and not force: REUSE it with 0 external API calls.
        - If embedding is missing (or force=True): generate authentic embedding via provider.
        - Never create synthetic or mock vectors in production.
        - On failure: preserve existing state, do NOT write synthetic vector, and raise.
        
        Returns:
            Tuple of (embedding_vector, generated_new: bool)
        """
        # 1. Reuse existing embedding if present
        if item.embedding is not None and not force:
            logger.debug(f"Reusing existing embedding for ContentItem {item.id} ('{item.title[:35]}')")
            return item.embedding, False

        # 2. Generate on-demand embedding
        emb_text = self.compile_embedding_text(item)
        try:
            embedding = await self.provider.generate_embedding(emb_text)
            async with self._db_lock:
                item.embedding = embedding
                item.embedding_model = self.provider.embedding_model_name
                await self.content_repo.update(item)

            logger.info(
                f"Generated authentic on-demand embedding for ContentItem {item.id} "
                f"(model: {self.provider.embedding_model_name}, dims: {len(embedding)})"
            )
            return embedding, True

        except Exception as exc:
            logger.error(
                f"On-demand embedding generation failed for ContentItem {item.id} ('{item.title[:35]}'): "
                f"{exc.__class__.__name__}: {str(exc)}"
            )
            raise

    async def process_item(
        self,
        item: ContentItem,
        force: bool = False,
        generate_embedding: bool = True,
    ) -> ContentItem:
        """Process a single ContentItem through AI analysis and optional on-demand embedding generation.
        
        Enforces idempotency: skips already completed items unless force=True.
        Guarantees fault isolation: preserves existing original data on failure.
        """
        # 1. Idempotency check
        has_completed_analysis = bool(item.ai_summary and item.processing_state == ProcessingStatus.COMPLETED.value)
        has_embedding = bool(item.embedding is not None)

        if not force:
            if generate_embedding and has_completed_analysis and has_embedding:
                logger.info(f"Skipping already fully processed ContentItem {item.id} ('{item.title[:40]}')")
                return item
            elif not generate_embedding and has_completed_analysis:
                logger.info(f"Skipping already analyzed ContentItem {item.id} ('{item.title[:40]}')")
                return item

        # Perform analysis if needed
        if not has_completed_analysis or force:
            await self.analyze_item(item, force=force)

        # Perform on-demand embedding if requested
        if generate_embedding and (item.embedding is None or force):
            await self.ensure_embedding(item, force=force)

        return item

    async def process_batch(
        self,
        items: List[ContentItem],
        force: bool = False,
    ) -> AIBatchProcessResponse:
        """Process a batch of ContentItems with bounded concurrency and per-item fault isolation."""
        semaphore = asyncio.Semaphore(self.concurrency)
        completed = 0
        skipped = 0
        failed = 0
        errors = []

        async def _process_bounded(item: ContentItem):
            nonlocal completed, skipped, failed
            # Pre-check skip
            if item.processing_state == ProcessingStatus.COMPLETED.value and not force:
                skipped += 1
                return

            async with semaphore:
                try:
                    await self.process_item(item, force=force)
                    completed += 1
                except Exception as exc:
                    failed += 1
                    err_msg = f"Item {item.id} ('{item.title[:30]}'): {exc.__class__.__name__}"
                    errors.append(err_msg)

        tasks = [_process_bounded(item) for item in items]
        await asyncio.gather(*tasks, return_exceptions=True)

        return AIBatchProcessResponse(
            total_candidates=len(items),
            completed=completed,
            skipped=skipped,
            failed=failed,
            errors=errors,
        )

    async def process_pending_batch(
        self,
        limit: int = 50,
        force: bool = False,
    ) -> AIBatchProcessResponse:
        """Fetch pending items from database and execute batch AI processing."""
        items = await self.content_repo.list_pending_ai_processing(limit=limit)
        return await self.process_batch(items, force=force)

    async def get_processing_status_summary(self) -> dict:
        """Return aggregate status counts across all content items."""
        return await self.content_repo.get_ai_processing_counts()
