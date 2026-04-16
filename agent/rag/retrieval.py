"""
Phase 4 — RAG retrieval pipeline (5 stages, NO Cohere).

Pipeline:
  1. Preprocess: normalize query, skip-RAG heuristic
  2. Hybrid search: vector (pgvector cosine) + BM25 (tsvector ts_rank) — top 20 each
  3. RRF fusion: k=60, merge to top 5
  4. Format context: [KB-1]..[KB-5] with source metadata
  5. Return formatted knowledge block for LLM injection
"""

import os
import re
from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI
from modules.logging_config import get_logger

log = get_logger("saransh.agent.rag.retrieval")

_client: Optional[OpenAI] = None


def _get_openai() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


# ── Stage 1: Preprocess ──────────────────────────────────

# Greetings and acknowledgments — skip RAG for these
_SKIP_PATTERNS = re.compile(
    r'^(hi|hello|hey|hii+|helo|ok|okay|yes|no|haan|nahi|nope|sure|thanks|thank you|'
    r'theek hai|accha|hmm+|good morning|good afternoon|good evening|namaste|'
    r'bye|tata|stop|start|👍|🙏|ok sir|ji|hnji|shukriya|dhanyavad)$',
    re.IGNORECASE
)


def should_skip_rag(text: str) -> bool:
    """
    Fast heuristic: skip RAG for very short messages or greetings/acknowledgments.
    Saves embedding cost + latency.
    """
    text = (text or "").strip()
    if len(text) < 8:
        return True
    if _SKIP_PATTERNS.match(text.strip()):
        return True
    return False


def _preprocess_query(text: str) -> str:
    """Normalize query for search. Light-touch — preserve Hindi/Hinglish."""
    text = text.strip()
    # Remove excessive punctuation
    text = re.sub(r'[!?]{2,}', '?', text)
    # Normalize whitespace
    text = re.sub(r'\s+', ' ', text)
    return text


# ── Stage 2: Hybrid search ───────────────────────────────

@dataclass
class ChunkResult:
    chunk_id: str
    document_id: str
    content: str
    doc_title: str
    source_type: str
    score: float = 0.0  # Used for RRF ranking


async def _vector_search(query_embedding: list[float], limit: int = 20) -> list[ChunkResult]:
    """Cosine similarity search via pgvector HNSW index."""
    from modules.db import _pool, _pool_ok

    if not _pool_ok():
        return []

    try:
        emb_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
        async with _pool.acquire() as conn:
            rows = await conn.fetch(
                '''SELECT kc.id, kc."documentId", kc.content,
                          kd.title, kd."sourceType",
                          1 - (kc.embedding <=> $1::vector) AS cosine_sim
                   FROM "KnowledgeChunk" kc
                   JOIN "KnowledgeDocument" kd ON kc."documentId" = kd.id
                   WHERE kd.enabled = true AND kc.embedding IS NOT NULL
                   ORDER BY kc.embedding <=> $1::vector
                   LIMIT $2''',
                emb_str, limit,
            )
            return [
                ChunkResult(
                    chunk_id=r["id"],
                    document_id=r["documentId"],
                    content=r["content"],
                    doc_title=r["title"],
                    source_type=r["sourceType"],
                    score=float(r["cosine_sim"]),
                )
                for r in rows
            ]
    except Exception as e:
        log.warning("rag.vector_search_error", error=str(e))
        return []


async def _bm25_search(query: str, limit: int = 20) -> list[ChunkResult]:
    """Full-text search via tsvector + ts_rank (BM25-style)."""
    from modules.db import _pool, _pool_ok

    if not _pool_ok():
        return []

    try:
        # Build tsquery — split words and OR them for flexibility
        words = re.findall(r'\w+', query.lower())
        if not words:
            return []
        # Use plainto_tsquery for robustness with mixed-language input
        async with _pool.acquire() as conn:
            rows = await conn.fetch(
                '''SELECT kc.id, kc."documentId", kc.content,
                          kd.title, kd."sourceType",
                          ts_rank(kc.tsv, plainto_tsquery('english', $1)) AS rank
                   FROM "KnowledgeChunk" kc
                   JOIN "KnowledgeDocument" kd ON kc."documentId" = kd.id
                   WHERE kd.enabled = true
                     AND kc.tsv @@ plainto_tsquery('english', $1)
                   ORDER BY rank DESC
                   LIMIT $2''',
                query, limit,
            )
            return [
                ChunkResult(
                    chunk_id=r["id"],
                    document_id=r["documentId"],
                    content=r["content"],
                    doc_title=r["title"],
                    source_type=r["sourceType"],
                    score=float(r["rank"]),
                )
                for r in rows
            ]
    except Exception as e:
        log.warning("rag.bm25_search_error", error=str(e))
        return []


# ── Stage 3: RRF fusion ──────────────────────────────────

def _rrf_fuse(
    vector_results: list[ChunkResult],
    bm25_results: list[ChunkResult],
    k: int = 60,
    top_n: int = 5,
) -> list[ChunkResult]:
    """
    Reciprocal Rank Fusion (RRF) to merge vector and BM25 results.
    score = sum(1 / (k + rank)) across both lists.
    """
    scores: dict[str, float] = {}
    chunks: dict[str, ChunkResult] = {}

    for rank, result in enumerate(vector_results):
        cid = result.chunk_id
        scores[cid] = scores.get(cid, 0) + 1.0 / (k + rank + 1)
        chunks[cid] = result

    for rank, result in enumerate(bm25_results):
        cid = result.chunk_id
        scores[cid] = scores.get(cid, 0) + 1.0 / (k + rank + 1)
        chunks[cid] = result

    # Sort by fused score descending
    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

    results = []
    for cid in sorted_ids[:top_n]:
        chunk = chunks[cid]
        chunk.score = scores[cid]
        results.append(chunk)

    return results


# ── Stage 4: Format context ──────────────────────────────

def _format_context(results: list[ChunkResult]) -> str:
    """Format RAG results as [KB-1]..[KB-N] citations for LLM injection."""
    if not results:
        return ""

    lines = []
    for i, r in enumerate(results, 1):
        lines.append(
            f"[KB-{i}] ({r.source_type}: {r.doc_title}, relevance: {r.score:.4f})\n{r.content}"
        )

    return "\n\n".join(lines)


# ── Stage 5: Main pipeline ───────────────────────────────

@dataclass
class RAGResult:
    context: str           # Formatted knowledge block for LLM
    chunks: list[ChunkResult]
    embedding_tokens: int = 0
    retrieval_hits: int = 0
    skipped: bool = False
    skip_reason: str = ""


async def rag_search(query: str) -> RAGResult:
    """
    Full 5-stage RAG pipeline.
    Returns formatted context block ready for LLM injection.
    """
    # Stage 1: Preprocess
    if should_skip_rag(query):
        log.info("rag.skipped", reason="heuristic", query_len=len(query))
        return RAGResult(context="", chunks=[], skipped=True, skip_reason="greeting/short")

    clean_query = _preprocess_query(query)

    # Stage 2a: Embed query
    try:
        client = _get_openai()
        emb_response = client.embeddings.create(
            model="text-embedding-3-small",
            input=[clean_query],
        )
        query_embedding = emb_response.data[0].embedding
        embedding_tokens = emb_response.usage.total_tokens
    except Exception as e:
        log.error("rag.embed_query_error", error=str(e))
        return RAGResult(context="", chunks=[], skipped=True, skip_reason=f"embed_error: {e}")

    # Stage 2b: Parallel hybrid search (vector + BM25)
    import asyncio
    vector_task = _vector_search(query_embedding, limit=20)
    bm25_task = _bm25_search(clean_query, limit=20)
    vector_results, bm25_results = await asyncio.gather(vector_task, bm25_task)

    log.info("rag.hybrid_search",
             vector_hits=len(vector_results),
             bm25_hits=len(bm25_results))

    # Stage 3: RRF fusion
    fused = _rrf_fuse(vector_results, bm25_results, k=60, top_n=5)

    if not fused:
        log.info("rag.no_results", query=clean_query[:50])
        return RAGResult(
            context="",
            chunks=[],
            embedding_tokens=embedding_tokens,
            retrieval_hits=0,
        )

    # Stage 4: Format context
    context = _format_context(fused)

    log.info("rag.context_injected",
             chunks=len(fused),
             top_score=fused[0].score if fused else 0,
             embedding_tokens=embedding_tokens)

    return RAGResult(
        context=context,
        chunks=fused,
        embedding_tokens=embedding_tokens,
        retrieval_hits=len(fused),
    )
