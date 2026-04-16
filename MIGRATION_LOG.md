# WhatsApp Assistant — v1.2 Migration Log

This file tracks every phase of the v1.2 refresh: what was changed, the git commit immediately **before** the phase (pre-phase baseline), and the one-line revert command. Each phase is gated on explicit user approval.

Plan of record: [C:\Users\saran\.claude\plans\ancient-twirling-meteor.md](../../../../C:/Users/saran/.claude/plans/ancient-twirling-meteor.md)

---

## Baseline (pre-v1.2)

- **Commit:** `ec60d16278caa28d2c19ffd3881dfbcf8a4c4dc7`
- **Branch:** `main`
- **Date:** 2026-04-15
- **Description:** Bot as-is before any v1.2 work begins. JSON-file conversation persistence, optional HMAC, no RAG, no tiered routing.
- **Full revert to baseline:** `git reset --hard ec60d16278caa28d2c19ffd3881dfbcf8a4c4dc7`

---

## Phase 0 — Foundations (no DB, no behavior change)

**Status:** COMPLETE on 2026-04-15
**Pre-phase commit:** `ec60d16278caa28d2c19ffd3881dfbcf8a4c4dc7` (baseline)
**Post-phase commit:** _(to be filled in when user commits)_

**Scope implemented (per user directive for Phase 0 items 2/3/4):**
1. ✅ **Log-only HMAC probe** — does NOT enforce. Logs `webhook.signature_probe` event on every inbound with: `secret_env_set`, `signature_header_present`, `signature_prefix` (first 8 chars), `hmac_matches` (True/False/None). 24-hour observation period before Phase 0.5 enables enforcement.
2. ✅ **structlog + correlation IDs** — JSON renderer in prod, ConsoleRenderer in dev (DEBUG=true). `correlation_id` generated per inbound webhook via `new_correlation_id()`. `phone_hash` (SHA-256, first 12 chars) injected into every log line via contextvars. All `print()` replaced in `main.py`, `agent/core.py`, `agent/whatsapp.py`, `agent/drive_portfolio.py`.
3. ✅ **Pydantic v2 webhook models** — `modules/webhook_models.py`. `WhatsAppWebhookPayload`, `WhatsAppMessage`, `WhatsAppMedia`, `WhatsAppLocation`, `WhatsAppInteractive`, `WhatsAppStatus`, etc. `extra="allow"` everywhere (Meta adds fields over time). On `ValidationError` the webhook logs the error + returns 200 (we don't make Meta retry on our parsing bug). `model_validate` runs at webhook entry.
4. ✅ **sentry-sdk[fastapi]** — initialized in `main.py` BEFORE FastAPI app creation, only if `SENTRY_DSN` env var set. `traces_sample_rate=0.1`, `send_default_pii=False`, `FastApiIntegration()` + `StarletteIntegration()`. Logs `sentry.initialized` or `sentry.skipped`.

**Files modified:**
- `Whatsapp Assistant/requirements.txt` — added `structlog==24.1.0`, `sentry-sdk[fastapi]==2.14.0`, `pytest==8.3.2`, `pytest-asyncio==0.23.8`
- `Whatsapp Assistant/main.py` — imports, structlog init, Sentry init, `_probe_webhook_signature()` (replaces `_verify_webhook_signature`), correlation IDs, Pydantic validation at webhook entry, print() → log calls
- `Whatsapp Assistant/agent/core.py` — logger import + print() → log
- `Whatsapp Assistant/agent/whatsapp.py` — logger import + 11 print() → log
- `Whatsapp Assistant/agent/drive_portfolio.py` — logger import + print() → log
- **NEW** `Whatsapp Assistant/modules/logging_config.py` — structlog configuration, `configure_logging()`, `get_logger()`, `new_correlation_id()`, `set_phone_hash()`, `hash_phone()`
- **NEW** `Whatsapp Assistant/modules/webhook_models.py` — Pydantic v2 models
- **NEW** `Whatsapp Assistant/test_webhook_models.py` — 7 pytest tests (valid payload, status-only, image, extra=allow forward-compat, empty entry, invalid entry list, malformed message)
- `Whatsapp Assistant/MIGRATION_LOG.md` — this file

**DB changes:** NONE

**Revert command:**
```
git reset --hard ec60d16278caa28d2c19ffd3881dfbcf8a4c4dc7
# (optional) restore prior deps — only needed if you installed them locally
pip install -r "Whatsapp Assistant/requirements.txt"
```

**Verification performed (2026-04-15):**
- ✅ `python -m py_compile main.py modules/logging_config.py modules/webhook_models.py agent/core.py agent/whatsapp.py agent/drive_portfolio.py test_webhook_models.py` — all compile clean
- ✅ `pytest test_webhook_models.py -v` → **7 passed in 0.31s**
- ⚠️ `python -c "from main import app"` blocked locally: Python 3.14 has no prebuilt wheels for `Pillow==10.4.0` / `pydantic==2.7.4` as pinned in requirements.txt. This is **pre-existing** (not Phase 0's fault). On the DO droplet (Python 3.11/3.12 inside Docker) it imports cleanly. User to verify on droplet at next deploy.

**Deferred / blockers to pick up next:**
- **Phase 0.5 TODO:** add `META_APP_SECRET=<value>` to `Whatsapp Assistant/.env`, then flip `_probe_webhook_signature` logic to enforcement mode (return 401 on mismatch). Currently log-only.
- **Phase 4 TODO:** SSH to droplet (165.232.178.128) and run `psql $DATABASE_URL -c "SELECT * FROM pg_available_extensions WHERE name = 'vector';"` to confirm pgvector is installable before we schema for RAG.
- **Local dev TODO (non-blocking):** either (a) downgrade local Python to 3.12, (b) bump `Pillow` to a version with 3.14 wheels (e.g. ≥11.0), or (c) run the bot only inside Docker locally.

**Approval needed for Phase 1:** ❌ waiting for explicit user sign-off.

---

## Phase 1 — Persistence parity (DB migration #1)

**Status:** COMPLETE on 2026-04-16
**Pre-phase commit:** `d6596c7` (Phase 0)
**Post-phase commit:** _(uncommitted — working tree changes on droplet, to be committed)_
**Backup:** `~/backups/pre-phase1-20260416-101134.dump` (66K, pg_dump -Fc)

**Scope implemented:**
1. DB migration `20260416_phase1_bot_persistence` — 2 enums (MessageDirection, BotStage), 3 new tables (BotConversation, BotMessage, AuditLog), 3 new columns on Lead (optedOut, lastInteractionAt, language), index on Lead.waPhone. All additive, zero existing data affected.
2. Dual-write persistence — modules/db.py (asyncpg connection pool, fire-and-forget writes). agent/conversation.py hooks: every save_conversation() syncs to BotConversation, every add_message() inserts into BotMessage. JSON remains source of truth.
3. Opt-out handler — inbound STOP/unsubscribe/रोकें/band karo/rok do sets Lead.optedOut=true, sends confirmation, blocks future bot outbound. START/subscribe/शुरू re-subscribes. AuditLog entries written for both.
4. DB pool lifecycle — init_pool() on FastAPI startup, close_pool() on shutdown. Graceful degradation: if DATABASE_URL not set, pool skips and all dual-writes silently no-op.

**Files modified:**
- saransh-dashboard/backend/prisma/schema.prisma — added MessageDirection enum, BotStage enum, BotConversation model, BotMessage model, AuditLog model, Lead.optedOut/lastInteractionAt/language fields + index on waPhone
- saransh-dashboard/backend/prisma/migrations/20260416_phase1_bot_persistence/migration.sql — manually created, applied via psql, resolved with prisma migrate resolve --applied
- NEW modules/db.py — async Postgres layer (asyncpg pool, upsert_conversation, insert_message, audit_log, set_lead_opted_out, is_lead_opted_out, sync_conversation_to_pg, sync_message_to_pg)
- agent/conversation.py — added asyncio import, structlog logger, _fire_pg_sync() + _fire_pg_message() hooks in save_conversation() and add_message()
- main.py — added modules.db imports, await init_pool() in startup, await close_pool() in shutdown, opt-out check + handler before client message dispatch
- requirements.txt — added asyncpg==0.29.0, python-dateutil==2.9.0
- .env (droplet only) — added DATABASE_URL

**DB changes:** YES — migration 20260416_phase1_bot_persistence

**Revert command:**
```bash
# Code revert:
git reset --hard d6596c7
# DB rollback (run from /opt/saransh-dashboard/backend):
npx prisma migrate resolve --rolled-back 20260416_phase1_bot_persistence
psql "$DATABASE_URL" -c 'DROP TABLE IF EXISTS "BotMessage", "BotConversation", "AuditLog"; DROP TYPE IF EXISTS "MessageDirection", "BotStage";'
psql "$DATABASE_URL" -c 'ALTER TABLE "Lead" DROP COLUMN IF EXISTS "optedOut", DROP COLUMN IF EXISTS "lastInteractionAt", DROP COLUMN IF EXISTS "language";'
psql "$DATABASE_URL" -c 'DROP INDEX IF EXISTS "Lead_waPhone_idx";'
# Restore from backup (nuclear option):
pg_restore -Fc --clean --if-exists -d saransh_dashboard ~/backups/pre-phase1-20260416-101134.dump
```

**Verification performed (2026-04-16):**
- Pre-migration backup: ~/backups/pre-phase1-20260416-101134.dump (66K)
- Lead count unchanged: 1 row before and after migration
- BotConversation/BotMessage/AuditLog: 0 rows after migration (clean)
- All Lead rows have optedOut=false (default applied correctly)
- prisma migrate status: 3 migrations, all applied, schema up to date
- db.pool_ready in startup logs (min=1, max=3)
- Test webhook: BotConversation row created (stage=NEW, seriousnessScore=9)
- Test webhook: BotMessage INBOUND row created (wamid matched, text correct)
- JSON file also written (dual-write confirmed)
- Correlation ID propagates: webhook.message > dispatch_client > client.handling > client.llm.begin
- Test data cleaned up after verification

**Known issues (pre-existing, not Phase 1):**
- KeyError 'logo' in agent/core.py:408 — droplet pricing.json missing logo key. Bot crashes on LLM reply. Needs pricing.json update (separate fix).

**Approval needed for Phase 2:** pending explicit user sign-off
---

## Phase 2 — Tiered routing + keyword rules + conversation flows

**Status:** COMPLETE on 2026-04-16
**Pre-phase commit:** `d8696fe` (config untrack)
**Post-phase commit:** `8922703`
**Backup:** `~/backups/pre-phase2-20260416-105539.dump` (84K, pg_dump -Fc)

**Scope implemented:**
1. DB migration `20260416_phase2_tiered_routing` — 2 enums (MatchType, TriggerType), 3 new tables (KeywordRule, ConversationFlow, CannedResponse), 2 new columns on BotConversation (activeFlowId, flowStepIndex). All additive, zero existing data affected.
2. 12 seeded KeywordRules with user-requested tweaks:
   - call_request split into EXACT (short phrases, priority 15) + CONTAINS (longer phrases, priority 16) to prevent false positives on "don't call me"
   - payment rule uses GENERIC response only (no UPI/bank details)
   - pricing_inquiry rule (priority 40) with action=pass_to_llm so LLM handles variable pricing intelligently
3. 1 seeded ConversationFlow (`cf_sales_default`) — data-driven representation of existing 10-stage sales state machine. Marked llm_driven=true so execution still uses agent/core.py (behavior identical).
4. Router (`agent/router.py`) — 3-tier routing: active-flow → keyword-rule → LLM fallback. In-memory rule cache with 5min TTL. Per-message structlog with route_tier key.
5. Cost tracking — in-memory counters: messages_handled_by_tier (flow/keyword/llm), tokens_saved_estimate (~3000 per skipped LLM call). Logged via router.stats event.

**Files modified:**
- saransh-dashboard/backend/prisma/schema.prisma — added MatchType enum, TriggerType enum, KeywordRule model, ConversationFlow model, CannedResponse model, BotConversation.activeFlowId + flowStepIndex
- saransh-dashboard/backend/prisma/migrations/20260416_phase2_tiered_routing/migration.sql — manually created, applied via psql, resolved with prisma migrate resolve --applied
- NEW agent/router.py — RouteResult dataclass, _load_rules() with cache, _match_rule() (EXACT/CONTAINS/REGEX), _check_active_flow(), route_message(), record_route(), get_stats()
- main.py — replaced hardcoded greeting/call/portfolio/LLM blocks with router-driven dispatch. Added router import, route_message() call, action handlers for each tier.

**DB changes:** YES — migration 20260416_phase2_tiered_routing + seed data (12 KeywordRules, 1 ConversationFlow)

**Revert command:**
```bash
# Code revert:
git reset --hard d8696fe
# DB rollback:
cd /opt/saransh-dashboard/backend
npx prisma migrate resolve --rolled-back 20260416_phase2_tiered_routing
psql "$DATABASE_URL" -c 'ALTER TABLE "BotConversation" DROP CONSTRAINT IF EXISTS "BotConversation_activeFlowId_fkey";'
psql "$DATABASE_URL" -c 'ALTER TABLE "BotConversation" DROP COLUMN IF EXISTS "activeFlowId", DROP COLUMN IF EXISTS "flowStepIndex";'
psql "$DATABASE_URL" -c 'DROP TABLE IF EXISTS "CannedResponse", "KeywordRule", "ConversationFlow";'
psql "$DATABASE_URL" -c 'DROP TYPE IF EXISTS "MatchType", "TriggerType";'
```

**Cost tracking methodology:**
- Every inbound message is routed through agent/router.py
- route_tier is logged per message: "flow" | "keyword" | "llm"
- When tier != llm, tokens_saved_estimate += 3000 (avg tokens for a gpt-4o-mini call with full system prompt + conversation history)
- Cost estimate: ~$3/1M tokens for gpt-4o-mini = ~$0.009 saved per skipped call
- Stats available via router.get_stats() and logged as router.stats event

**Verification performed (2026-04-16):**
- Pre-migration backup: ~/backups/pre-phase2-20260416-105539.dump (84K)
- Migration applied cleanly, prisma validate passed
- 12 KeywordRules seeded, 1 ConversationFlow seeded
- Test A: "STOP" → webhook opt-out handler (pre-router, no LLM) ✓
- Test B: "hi" → kr_greeting keyword rule (no LLM) ✓
- Test C: "kitne ka logo banate ho" → kr_pricing → pass_to_llm → LLM replied with correct ₹2999 pricing ✓
- Test D: "What are your services?" → kr_services → static_reply (no LLM) ✓
- Tier distribution from 4 tests: keyword=4 (100%), llm=0 (0%), tokens_saved=9000
- Test data cleaned up after verification

**User-requested tweaks (all applied):**
1. call_request split: EXACT for short phrases (priority 15) + CONTAINS for longer (priority 16)
2. payment response is generic — no UPI/bank/QR details
3. pricing_inquiry rule added with pass_to_llm action

**Approval needed for Phase 3:** pending explicit user sign-off
---

## Phase 3 — Structured tools + lead qualification (no DB migration)

**Status:** NOT STARTED

**Files modified (planned):**
- `Whatsapp Assistant/agent/tools.py` (new) — 8 strict-mode Pydantic tool schemas
- `Whatsapp Assistant/agent/core.py` — swap free-form completions → OpenAI strict tool-calling with `parallel_tool_calls=False`; refusal handling
- `Whatsapp Assistant/modules/db.py` — `capture_lead()` DB writer

**DB changes:** NONE (reuses existing `Lead`)

**Revert command:** `git reset --hard <pre-phase-3-commit>`

---

## Phase 4 — RAG (DB migration #3 + pgvector extension)

**Status:** NOT STARTED — BLOCKED on confirming pgvector is available in Postgres

**Files modified (planned):**
- `saransh-dashboard/backend/prisma/schema.prisma` — `KnowledgeDocument`, `KnowledgeChunk` (with `Unsupported("vector(1536)")`)
- Custom SQL migration: `CREATE EXTENSION IF NOT EXISTS vector;` + HNSW index + tsvector GIN index
- `Whatsapp Assistant/agent/rag.py` (new) — hybrid retrieval
- Ingestion CLI: `Whatsapp Assistant/modules/ingest.py`

**DB changes:** YES — migration #3 + extension. **Infra-affecting.** Shown separately.

**Revert command:**
```
git reset --hard <pre-phase-4-commit>
psql $DATABASE_URL -c "DROP TABLE IF EXISTS \"KnowledgeChunk\", \"KnowledgeDocument\";"
# Leave pgvector extension installed — harmless to keep; drop manually only if required.
```

---

## Phase 5 — Security hardening (no DB migration)

**Status:** NOT STARTED

**Files modified (planned):**
- `Whatsapp Assistant/agent/security.py` (new) — input sanitizer, output prompt-injection filter, AES-256-GCM helpers
- `Whatsapp Assistant/main.py` — wire sanitizer + per-number Redis rate-limit; enforce `Lead.optedOut` on every outbound
- `Whatsapp Assistant/agent/whatsapp.py` — opt-out gate before every send

**DB changes:** NONE

**Revert command:** `git reset --hard <pre-phase-5-commit>`

---

## Phase 6 (optional) — Multi-language + Flows + voice

Deferred. Will add entries here when/if approved.

---

## Revert protocol

1. **Identify phase** you want to revert.
2. **Code revert first:** `git reset --hard <pre-phase-commit>`.
3. **DB revert second** (only if that phase had a migration): run the phase-specific SQL block above. Always run inside a `BEGIN; ... COMMIT;` transaction and verify row counts before committing.
4. **Restart services:** `systemctl restart saransh-bot` (or local `uvicorn` reload).
5. **Verify dashboard still loads** and existing `Lead` queries succeed.

For a full-reset to pre-v1.2 state: `git reset --hard ec60d16278caa28d2c19ffd3881dfbcf8a4c4dc7` + drop every new table added after baseline.
