"""
Phase 4 — RAG (Retrieval-Augmented Generation) module.

5-stage pipeline (NO Cohere, OpenAI only):
  1. Preprocess query (normalize, intent check)
  2. Hybrid search (vector cosine + BM25 tsvector) — top 20 each
  3. RRF fusion (k=60) — merge to top 5
  4. Format context with citations [KB-1]..[KB-5]
  5. Inject into LLM prompt as knowledge block
"""

from agent.rag.retrieval import rag_search, should_skip_rag
from agent.rag.ingestion import ingest_document, ingest_documents_from_settings, re_embed_document

__all__ = [
    "rag_search",
    "should_skip_rag",
    "ingest_document",
    "ingest_documents_from_settings",
    "re_embed_document",
]
