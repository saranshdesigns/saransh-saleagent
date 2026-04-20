"""
Phase 1 — Async Postgres dual-write layer.

Provides a connection pool and helpers to write BotConversation / BotMessage /
AuditLog rows alongside the existing JSON file persistence.

All writes are fire-and-forget with error logging — Postgres is secondary
until Phase 2 stabilises. JSON remains source of truth.
"""

import os
import asyncio
from datetime import datetime, timezone
from typing import Optional

import asyncpg
from modules.logging_config import get_logger

log = get_logger("saransh.modules.db")

_pool: Optional[asyncpg.Pool] = None


# ── Pool lifecycle ──────────────────────────────────────

async def init_pool() -> None:
    """Call once at app startup (lifespan)."""
    global _pool
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        log.warning("db.pool_skip", reason="DATABASE_URL not set")
        return
    # asyncpg needs postgresql:// not postgres://
    if dsn.startswith("postgres://"):
        dsn = dsn.replace("postgres://", "postgresql://", 1)
    try:
        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
        log.info("db.pool_ready", min_size=1, max_size=3)
    except Exception as e:
        log.error("db.pool_init_failed", error=str(e))
        _pool = None


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        log.info("db.pool_closed")


def _pool_ok() -> bool:
    return _pool is not None


# ── Helpers ─────────────────────────────────────────────

def _utcnow() -> datetime:
    """Naive UTC datetime — asyncpg + Prisma TIMESTAMP(3) expects no tzinfo."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _cuid() -> str:
    """Generate a cuid-like ID (matches Prisma's @default(cuid()))."""
    import hashlib, time, random, string
    seed = f"{time.time_ns()}{random.random()}{''.join(random.choices(string.ascii_lowercase, k=4))}"
    return "c" + hashlib.sha256(seed.encode()).hexdigest()[:24]


STAGE_MAP = {
    "new": "NEW",
    "identifying_service": "IDENTIFYING_SERVICE",
    "collecting_details": "COLLECTING_DETAILS",
    "confirming_details": "CONFIRMING_DETAILS",
    "presenting_pricing": "PRESENTING_PRICING",
    "handling_objection": "HANDLING_OBJECTION",
    "negotiating": "NEGOTIATING",
    "pricing_confirmed": "PRICING_CONFIRMED",
    "handoff": "HANDOFF",
    "escalated": "ESCALATED",
    "closed": "CLOSED",
}


# ── BotConversation ────────────────────────────────────

async def upsert_conversation(
    wa_phone: str,
    stage: str,
    collected_details: Optional[dict] = None,
    seriousness_score: Optional[int] = None,
    agreed_price: Optional[float] = None,
    handoff_triggered: bool = False,
    direction: str = "INBOUND",
) -> Optional[str]:
    """
    Upsert a BotConversation row by waPhone.
    Returns the conversation ID, or None on failure.
    """
    if not _pool_ok():
        return None
    try:
        pg_stage = STAGE_MAP.get(stage, "NEW")
        now = _utcnow()
        import json as _json
        details_json = _json.dumps(collected_details) if collected_details else None

        async with _pool.acquire() as conn:
            # Phase 1.1: look up matching live Lead so we can populate BotConversation.leadId
            lead_row = await conn.fetchrow(
                'SELECT id FROM "Lead" WHERE "waPhone" = $1 AND "deletedAt" IS NULL',
                wa_phone,
            )
            matched_lead_id = lead_row["id"] if lead_row else None

            # Check for existing active conversation for this phone
            row = await conn.fetchrow(
                'SELECT id, "leadId" FROM "BotConversation" WHERE "waPhone" = $1 ORDER BY "createdAt" DESC LIMIT 1',
                wa_phone,
            )
            if row:
                conv_id = row["id"]
                update_ts = "\"lastInboundAt\"" if direction == "INBOUND" else "\"lastOutboundAt\""
                await conn.execute(
                    f'''UPDATE "BotConversation"
                        SET stage = $1::\"BotStage\",
                            "collectedDetails" = COALESCE($2::jsonb, "collectedDetails"),
                            "seriousnessScore" = COALESCE($3, "seriousnessScore"),
                            "agreedPrice" = COALESCE($4, "agreedPrice"),
                            "handoffTriggered" = $5,
                            "leadId" = COALESCE("leadId", $8),
                            {update_ts} = $6,
                            "updatedAt" = $6
                        WHERE id = $7''',
                    pg_stage, details_json, seriousness_score,
                    agreed_price, handoff_triggered, now, conv_id, matched_lead_id,
                )
                return conv_id
            else:
                conv_id = _cuid()
                inbound_at = now if direction == "INBOUND" else None
                outbound_at = now if direction == "OUTBOUND" else None
                await conn.execute(
                    '''INSERT INTO "BotConversation"
                        (id, "waPhone", "leadId", stage, "collectedDetails",
                         "seriousnessScore", "agreedPrice", "handoffTriggered",
                         "lastInboundAt", "lastOutboundAt", "createdAt", "updatedAt")
                        VALUES ($1, $2, $3, $4::\"BotStage\", $5::jsonb, $6, $7, $8, $9, $10, $11, $11)''',
                    conv_id, wa_phone, matched_lead_id, pg_stage, details_json,
                    seriousness_score, agreed_price, handoff_triggered,
                    inbound_at, outbound_at, now,
                )
                return conv_id
    except Exception as e:
        log.warning("db.upsert_conversation_error", error=str(e), phone_len=len(wa_phone))
        return None


# ── BotMessage ─────────────────────────────────────────

async def insert_message(
    conversation_id: str,
    direction: str,
    text: Optional[str] = None,
    media_type: Optional[str] = None,
    media_url: Optional[str] = None,
    wamid: Optional[str] = None,
) -> Optional[str]:
    """Insert a BotMessage row. Returns message ID or None on failure."""
    if not _pool_ok() or not conversation_id:
        return None
    try:
        msg_id = _cuid()
        now = _utcnow()
        async with _pool.acquire() as conn:
            await conn.execute(
                '''INSERT INTO "BotMessage"
                    (id, "conversationId", direction, text, "mediaType", "mediaUrl", wamid, "sentAt")
                    VALUES ($1, $2, $3::"MessageDirection", $4, $5, $6, $7, $8)
                    ON CONFLICT (wamid) DO NOTHING''',
                msg_id, conversation_id, direction, text, media_type, media_url, wamid, now,
            )
        return msg_id
    except Exception as e:
        log.warning("db.insert_message_error", error=str(e))
        return None


# ── AuditLog ───────────────────────────────────────────

async def audit_log(
    actor: str,
    action: str,
    entity_type: str,
    entity_id: Optional[str] = None,
    before_json: Optional[dict] = None,
    after_json: Optional[dict] = None,
) -> None:
    """Write an audit log entry. Fire-and-forget."""
    if not _pool_ok():
        return
    try:
        import json as _json
        async with _pool.acquire() as conn:
            await conn.execute(
                '''INSERT INTO "AuditLog"
                    (id, actor, action, "entityType", "entityId", "beforeJson", "afterJson", "createdAt")
                    VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8)''',
                _cuid(), actor, action, entity_type, entity_id,
                _json.dumps(before_json) if before_json else None,
                _json.dumps(after_json) if after_json else None,
                _utcnow(),
            )
    except Exception as e:
        log.warning("db.audit_log_error", error=str(e))


# ── Lead opt-out ───────────────────────────────────────

async def set_lead_opted_out(wa_phone: str, opted_out: bool = True) -> bool:
    """Set Lead.optedOut by waPhone. Returns True if a row was updated."""
    if not _pool_ok():
        return False
    try:
        async with _pool.acquire() as conn:
            result = await conn.execute(
                'UPDATE "Lead" SET "optedOut" = $1, "updatedAt" = $2 WHERE "waPhone" = $3 AND "deletedAt" IS NULL',
                opted_out, _utcnow(), wa_phone,
            )
            updated = int(result.split()[-1]) > 0
            if updated:
                log.info("db.lead_opted_out", phone_len=len(wa_phone), opted_out=opted_out)
            return updated
    except Exception as e:
        log.warning("db.set_lead_opted_out_error", error=str(e))
        return False


async def is_lead_opted_out(wa_phone: str) -> bool:
    """Check if a lead has opted out."""
    if not _pool_ok():
        return False
    try:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT "optedOut" FROM "Lead" WHERE "waPhone" = $1 AND "deletedAt" IS NULL',
                wa_phone,
            )
            return bool(row and row["optedOut"])
    except Exception as e:
        log.warning("db.is_opted_out_error", error=str(e))
        return False


# ── Convenience: dual-write from conversation layer ────

async def sync_conversation_to_pg(phone: str, conv: dict, direction: str = "INBOUND") -> Optional[str]:
    """
    Called after every JSON save. Syncs the full conversation state to Postgres.
    Returns the BotConversation ID.
    """
    return await upsert_conversation(
        wa_phone=phone,
        stage=conv.get("stage", "new"),
        collected_details=conv.get("collected_details"),
        seriousness_score=conv.get("seriousness_score"),
        agreed_price=conv.get("agreed_price"),
        handoff_triggered=conv.get("handoff_triggered", False),
        direction=direction,
    )


async def sync_message_to_pg(
    phone: str,
    role: str,
    content: str,
    wamid: Optional[str] = None,
    media_type: Optional[str] = None,
    media_url: Optional[str] = None,
) -> None:
    """
    Called after every add_message(). Writes the message to BotMessage.
    Finds or creates the BotConversation first.
    """
    direction = "INBOUND" if role == "user" else "OUTBOUND"

    # Get or create conversation
    conv_id = await upsert_conversation(
        wa_phone=phone,
        stage="new",  # will be updated by sync_conversation_to_pg
        direction=direction,
    )
    if not conv_id:
        return

    await insert_message(
        conversation_id=conv_id,
        direction=direction,
        text=content[:4000] if content else None,
        media_type=media_type,
        media_url=media_url,
        wamid=wamid,
    )
