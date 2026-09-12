"""Ingestion framework, adapters, canonicalizers, and orchestration."""
from app.ingestion.base import BaseIngestionAdapter
from app.ingestion.canonicalizer import canonicalize_url, generate_content_hash
from app.ingestion.orchestrator import IngestionOrchestrator

__all__ = [
    "BaseIngestionAdapter",
    "canonicalize_url",
    "generate_content_hash",
    "IngestionOrchestrator",
]
