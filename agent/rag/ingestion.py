"""
Phase 4 — RAG ingestion pipeline.

Chunks documents (500-800 tokens), embeds via text-embedding-3-small,
stores in KnowledgeDocument + KnowledgeChunk tables.
"""

import os
import json
import hashlib
import re
from typing import Optional

from openai import OpenAI
from modules.logging_config import get_logger

log = get_logger("saransh.agent.rag.ingestion")

_client: Optional[OpenAI] = None


def _get_openai() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


def _cuid() -> str:
    """Generate a cuid-like ID."""
    import time, random, string
    seed = f"{time.time_ns()}{random.random()}{''.join(random.choices(string.ascii_lowercase, k=4))}"
    return "c" + hashlib.sha256(seed.encode()).hexdigest()[:24]


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English, ~3 for Hindi/mixed."""
    return max(1, len(text) // 3)


def _chunk_text(text: str, min_tokens: int = 400, max_tokens: int = 800) -> list[str]:
    """
    Split text into chunks of 400-800 tokens using semantic boundaries.
    Prefers paragraph/section splits. Falls back to sentence splits.
    """
    # If the whole text fits in one chunk, return as-is
    if _estimate_tokens(text) <= max_tokens:
        return [text.strip()] if text.strip() else []

    # Split by double newlines (paragraphs) first
    paragraphs = re.split(r'\n\s*\n', text)
    chunks = []
    current_chunk = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        candidate = (current_chunk + "\n\n" + para).strip() if current_chunk else para

        if _estimate_tokens(candidate) <= max_tokens:
            current_chunk = candidate
        else:
            # Current chunk is big enough, save it
            if current_chunk and _estimate_tokens(current_chunk) >= min_tokens:
                chunks.append(current_chunk)
                current_chunk = para
            elif current_chunk:
                # Current chunk too small, try to add paragraph by sentences
                sentences = re.split(r'(?<=[.!?।])\s+', para)
                for sent in sentences:
                    test = (current_chunk + " " + sent).strip() if current_chunk else sent
                    if _estimate_tokens(test) <= max_tokens:
                        current_chunk = test
                    else:
                        if current_chunk:
                            chunks.append(current_chunk)
                        current_chunk = sent
            else:
                # Single paragraph too long — split by sentences
                sentences = re.split(r'(?<=[.!?।])\s+', para)
                for sent in sentences:
                    test = (current_chunk + " " + sent).strip() if current_chunk else sent
                    if _estimate_tokens(test) <= max_tokens:
                        current_chunk = test
                    else:
                        if current_chunk:
                            chunks.append(current_chunk)
                        current_chunk = sent

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


async def _embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts using text-embedding-3-small."""
    if not texts:
        return []

    client = _get_openai()
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=texts,
    )
    log.info("rag.embed", count=len(texts),
             total_tokens=response.usage.total_tokens)
    return [item.embedding for item in response.data]


async def ingest_document(
    title: str,
    content: str,
    source_type: str = "CUSTOM",
    source_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Optional[str]:
    """
    Ingest a document: chunk, embed, store in KnowledgeDocument + KnowledgeChunk.
    Returns the document ID, or None on failure.
    """
    from modules.db import _pool, _pool_ok

    if not _pool_ok():
        log.warning("rag.ingest_skip", reason="no db pool")
        return None

    if not content.strip():
        log.warning("rag.ingest_skip", reason="empty content", title=title)
        return None

    try:
        doc_id = _cuid()
        chunks = _chunk_text(content)

        if not chunks:
            log.warning("rag.ingest_skip", reason="no chunks", title=title)
            return None

        # Embed all chunks
        embeddings = await _embed_texts(chunks)

        async with _pool.acquire() as conn:
            # Insert document
            await conn.execute(
                '''INSERT INTO "KnowledgeDocument"
                    (id, title, content, "sourceType", "sourceId", metadata, enabled, "createdAt", "updatedAt")
                    VALUES ($1, $2, $3, $4::"KnowledgeSourceType", $5, $6::jsonb, true, NOW(), NOW())''',
                doc_id, title, content, source_type, source_id,
                json.dumps(metadata) if metadata else None,
            )

            # Insert chunks with embeddings
            for i, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
                chunk_id = _cuid()
                token_count = _estimate_tokens(chunk_text)
                # Convert embedding list to pgvector format string
                emb_str = "[" + ",".join(str(x) for x in embedding) + "]"
                await conn.execute(
                    '''INSERT INTO "KnowledgeChunk"
                        (id, "documentId", "chunkIndex", content, embedding, "tokenCount", "createdAt")
                        VALUES ($1, $2, $3, $4, $5::vector, $6, NOW())''',
                    chunk_id, doc_id, i, chunk_text, emb_str, token_count,
                )

        log.info("rag.ingested", doc_id=doc_id, title=title,
                 chunks=len(chunks), source_type=source_type)
        return doc_id

    except Exception as e:
        log.error("rag.ingest_error", error=str(e), title=title)
        return None


async def re_embed_document(doc_id: str) -> bool:
    """Delete old chunks and re-ingest from document content."""
    from modules.db import _pool, _pool_ok

    if not _pool_ok():
        return False

    try:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT title, content, "sourceType", "sourceId", metadata FROM "KnowledgeDocument" WHERE id = $1',
                doc_id,
            )
            if not row:
                return False

            # Delete old chunks
            await conn.execute(
                'DELETE FROM "KnowledgeChunk" WHERE "documentId" = $1', doc_id
            )

        # Re-ingest chunks
        chunks = _chunk_text(row["content"])
        if not chunks:
            return False

        embeddings = await _embed_texts(chunks)

        async with _pool.acquire() as conn:
            for i, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
                chunk_id = _cuid()
                token_count = _estimate_tokens(chunk_text)
                emb_str = "[" + ",".join(str(x) for x in embedding) + "]"
                await conn.execute(
                    '''INSERT INTO "KnowledgeChunk"
                        (id, "documentId", "chunkIndex", content, embedding, "tokenCount", "createdAt")
                        VALUES ($1, $2, $3, $4, $5::vector, $6, NOW())''',
                    chunk_id, doc_id, i, chunk_text, emb_str, token_count,
                )

        log.info("rag.re_embedded", doc_id=doc_id, chunks=len(chunks))
        return True

    except Exception as e:
        log.error("rag.re_embed_error", error=str(e), doc_id=doc_id)
        return False


async def ingest_documents_from_settings() -> dict:
    """
    Seed KnowledgeDocuments from config/settings.json KB entries + pricing.json.
    Skips documents that already exist (by sourceId).
    Returns stats dict.
    """
    from modules.db import _pool, _pool_ok

    if not _pool_ok():
        return {"error": "no db pool"}

    stats = {"ingested": 0, "skipped": 0, "errors": 0}

    try:
        settings_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "settings.json")
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)

        kb = settings.get("knowledge_base", [])

        for entry in kb:
            entry_id = str(entry.get("id", ""))
            question = entry.get("question", "")
            answer = entry.get("answer", "")

            if not answer.strip():
                stats["skipped"] += 1
                continue

            # Determine source type from question prefix
            if "[service-sync]" in question:
                source_type = "SERVICE"
                title = question.replace("[service-sync] ", "")
            elif "[faq-sync]" in question:
                source_type = "FAQ"
                title = question.replace("[faq-sync] ", "")
            elif "[testimonial-sync]" in question:
                source_type = "TESTIMONIAL"
                title = question.replace("[testimonial-sync] ", "")
            elif "[policy-sync]" in question:
                source_type = "POLICY"
                title = question.replace("[policy-sync] ", "")
            else:
                source_type = "CUSTOM"
                title = question

            source_id = f"settings_kb_{entry_id}"

            # Check if already exists
            async with _pool.acquire() as conn:
                existing = await conn.fetchval(
                    'SELECT id FROM "KnowledgeDocument" WHERE "sourceId" = $1',
                    source_id,
                )
                if existing:
                    stats["skipped"] += 1
                    continue

            # Combine question + answer for richer content
            full_content = f"{title}\n\n{answer}"
            doc_id = await ingest_document(
                title=title,
                content=full_content,
                source_type=source_type,
                source_id=source_id,
                metadata={"kb_id": entry_id, "original_question": question},
            )

            if doc_id:
                stats["ingested"] += 1
            else:
                stats["errors"] += 1

    except Exception as e:
        log.error("rag.seed_settings_error", error=str(e))
        stats["errors"] += 1

    # Also ingest pricing data
    try:
        pricing_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "pricing.json")
        with open(pricing_path, "r", encoding="utf-8") as f:
            pricing = json.load(f)

        # Logo pricing
        logo_content = f"""Logo Design Pricing

Logo Package: ₹{pricing['logo']['logo_package']['price']} (minimum ₹{pricing['logo']['logo_package']['min_price']})
Includes: 3 logo concepts, primary logo, secondary logo, submark/monogram, favicon, 5 revisions.
Final deliverables: PNG, JPEG, PDF, SVG & AI formats.

Total Branding Package: ₹{pricing['logo']['branding_package']['price']}
Includes everything in Logo Package plus brand guidelines, color palette, typography system."""

        source_id = "pricing_logo"
        async with _pool.acquire() as conn:
            existing = await conn.fetchval(
                'SELECT id FROM "KnowledgeDocument" WHERE "sourceId" = $1', source_id
            )
        if not existing:
            doc_id = await ingest_document(
                title="Logo Design Pricing",
                content=logo_content,
                source_type="SERVICE",
                source_id=source_id,
                metadata={"category": "logo"},
            )
            if doc_id:
                stats["ingested"] += 1

        # Packaging pricing
        pkg = pricing["packaging"]
        packaging_content = f"""Packaging Design Pricing

Pouch Design:
- Master design (first unique): ₹{pkg['pouch']['master']['price']} (min ₹{pkg['pouch']['master']['min_price']})
- Variant (same layout, different flavor): ₹{pkg['pouch']['variant']['price']} (min ₹{pkg['pouch']['variant']['min_price']})

Box Design:
- Master design: ₹{pkg['box']['master']['price']} (min ₹{pkg['box']['master']['min_price']})
- Variant: ₹{pkg['box']['variant']['price']} (min ₹{pkg['box']['variant']['min_price']})

Label Design:
- Master design: ₹{pkg['label']['master']['price']} (min ₹{pkg['label']['master']['min_price']})
- Variant: ₹{pkg['label']['variant']['price']} (min ₹{pkg['label']['variant']['min_price']})

Size Change: ₹500 (Pouch/Box), ₹400 (Label) — fixed, non-negotiable.

Deliverables per design: Print-ready PDF (CMYK, bleed marks), editable AI file, PNG/JPEG previews. 3 rounds of revisions included."""

        source_id = "pricing_packaging"
        async with _pool.acquire() as conn:
            existing = await conn.fetchval(
                'SELECT id FROM "KnowledgeDocument" WHERE "sourceId" = $1', source_id
            )
        if not existing:
            doc_id = await ingest_document(
                title="Packaging Design Pricing",
                content=packaging_content,
                source_type="SERVICE",
                source_id=source_id,
                metadata={"category": "packaging"},
            )
            if doc_id:
                stats["ingested"] += 1

        # Website pricing
        web = pricing["website"]
        website_content = f"""Website Design Pricing

Starter Package: ₹{web['starter']['price_min']}–₹{web['starter']['price_max']}
Advance: ₹{web['starter']['advance']}. Best for small businesses, 1-page website. Delivery: 2-3 days.

Business Package: ₹{web['business']['price_min']}–₹{web['business']['price_max']}
Advance: ₹{web['business']['advance']}. 5-8 pages, best for service providers. Delivery: 5-7 days.

Premium Package: ₹{web['premium']['price_min']}–₹{web['premium']['price_max']}
Advance: ₹{web['premium']['advance']}. 8-12 pages, best for established brands. Delivery: 7-10 days.

Ecommerce/Shopify: ₹{web['ecommerce']['price_min']}–₹{web['ecommerce']['price_max']}
Advance: ₹{web['ecommerce']['advance']}. For online selling. Delivery: 7-10 days.

All packages include: Custom design, mobile-responsive, SSL, CDN, basic SEO. Timeline starts after advance payment and materials received."""

        source_id = "pricing_website"
        async with _pool.acquire() as conn:
            existing = await conn.fetchval(
                'SELECT id FROM "KnowledgeDocument" WHERE "sourceId" = $1', source_id
            )
        if not existing:
            doc_id = await ingest_document(
                title="Website Design Pricing",
                content=website_content,
                source_type="SERVICE",
                source_id=source_id,
                metadata={"category": "website"},
            )
            if doc_id:
                stats["ingested"] += 1

        # Business info
        business_content = """About SaranshDesigns

SaranshDesigns is a professional freelance branding studio run by Saransh Sharma.

Services offered (only these three):
1. Logo Design — brand identity, wordmarks, emblems, minimal logos
2. Packaging Design — pouches, boxes, labels, jars, sachets for food/FMCG/cosmetics
3. Website Design — starter to ecommerce, responsive, fast-loading

Process:
1. Client inquiry via WhatsApp or website
2. AI assistant qualifies the lead and collects basic details
3. Pricing presented and negotiated
4. Client agrees → connected with Saransh Sharma sir
5. Advance payment collected by Saransh
6. Full project details collected
7. Design work begins
8. Concepts delivered → revisions → final files

Industries served: Doctors, clinics, salons, gyms, coaching classes, consultants, lawyers, CAs, freelancers, startups, manufacturers, traders, B2B suppliers, real estate agents, interior designers.

Industries NOT served: Alcohol brands, non-vegetarian food brands.

Monthly maintenance: ₹500–₹3,000/month depending on service. No per-user fees.

Portfolio links:
- Website: https://saransh.space/
- Behance: https://www.behance.net/SaranshDesigns
- Instagram: https://www.instagram.com/saranshdesigns"""

        source_id = "business_info"
        async with _pool.acquire() as conn:
            existing = await conn.fetchval(
                'SELECT id FROM "KnowledgeDocument" WHERE "sourceId" = $1', source_id
            )
        if not existing:
            doc_id = await ingest_document(
                title="About SaranshDesigns",
                content=business_content,
                source_type="BUSINESS_INFO",
                source_id=source_id,
            )
            if doc_id:
                stats["ingested"] += 1

    except Exception as e:
        log.error("rag.seed_pricing_error", error=str(e))
        stats["errors"] += 1

    log.info("rag.seed_complete", **stats)
    return stats
