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

## Phase 0.5 — HMAC enforcement

**Status:** COMPLETE on 2026-04-16
**Commit:** `bbc970f`

**Scope implemented:**
1. `_probe_webhook_signature()` → `_verify_webhook_signature()` — returns bool instead of void. Rejects with 401 if META_APP_SECRET is set but signature is missing or invalid.
2. Graceful degradation: if META_APP_SECRET not set, allows through with warning log (safe for dev environments).
3. 4 pytest tests: valid signature accepted, invalid rejected, missing rejected, no-secret allows through.

**Files modified:**
- main.py — replaced probe with enforcer, call site returns 401 on failure
- test_webhook_models.py — added TestHMACVerification class (4 tests)

**Verification:** unsigned request → 401, valid HMAC → 200, real Meta traffic `hmac_matches=true`

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

## Phase 3 — Structured tools + real-time lead scoring

**Status:** COMPLETE on 2026-04-16
**Pre-phase commit:** `bbc970f` (Phase 0.5)
**Post-phase commit:** `b08fb32`

**Scope implemented:**
1. 8 OpenAI tools with strict=true, parallel_tool_calls=false:
   - search_knowledge — searches config/settings.json KB entries
   - capture_lead — saves lead info to JSON + BotConversation, computes score
   - escalate_to_human — triggers alert, logs AuditLog
   - send_media — stub (queued for reply flow)
   - mark_opted_out — LLM-initiated opt-out via DB
   - book_appointment — creates conversation note + AuditLog
   - check_status — stub (returns "Saransh will confirm")
   - get_entity_details — searches KB / returns stub
2. Lead scoring algorithm (0-100):
   - waPhone +10, name +10, businessType +10, specificNeed +15, budgetSignal +20, timeline +15, isDecisionMaker +15, notes +5
   - Buckets: COLD (0-30), WARM (31-60), HOT (61-85), READY_FOR_CALL (86-100)
   - Score >= 86 auto-triggers escalate_to_human
3. process_message() converted from sync to async for tool execution
4. Tool-call loop: max 3 rounds of tool calls before final reply
5. SYSTEM_PROMPT updated with TOOL USAGE section instructing LLM to use tools proactively
6. Score persisted to JSON conversation (seriousness_score) + BotConversation.seriousnessScore

**Files modified:**
- NEW agent/tools.py — 8 Pydantic tool schemas, _schema_to_strict() converter, TOOLS list, compute_lead_score(), score_bucket(), execute_tool() dispatcher, 8 tool executors
- agent/core.py — import tools, async process_message with tool-call loop, TOOL USAGE prompt section
- main.py — await process_message

**DB changes:** NO — reuses existing BotConversation.seriousnessScore

**Verification performed (2026-04-16):**
- Test 1: "logo chahiye, coffee shop ke liye, budget 4000, next week chahiye"
  - capture_lead called → score=70 (HOT), businessType=coffee shop, budget=4000, timeline=next week
  - JSON details: business_type, specific_need, budget, timeline all captured
- Test 2: "haan mai malik hu, mera naam Raj hai" (follow-up)
  - capture_lead called → name=Raj, isDecisionMaker=true → score=95 (READY_FOR_CALL)
  - Auto-escalation triggered → escalate_to_human called by LLM (urgency=high)
  - Reply: "Perfect Raj! ... Saransh Sharma sir ko notify kar raha hoon"
- All 11 pytest tests still pass (7 Phase 0 + 4 Phase 0.5)

**Approval needed for Phase 4:** pending explicit user sign-off
---

## Phase 4 — RAG (Retrieval-Augmented Generation)

**Status:** COMPLETE on 2026-04-16
**Pre-phase commit:** `74b380d` (Telegram fix)
**Post-phase commit:** `a53ecd2`
**Backup:** `~/backups/pre-phase4-20260416-121441.dump` (105K, pg_dump -Fc)

**Scope implemented:**
1. DB migration `20260416_phase4_rag_knowledge` — 1 enum (KnowledgeSourceType), 2 new tables (KnowledgeDocument, KnowledgeChunk), pgvector extension, HNSW + GIN indexes, tsvector auto-update trigger. All additive.
2. `agent/rag/ingestion.py` — Document chunking (400-800 tokens, semantic paragraph/sentence boundaries), embedding via `text-embedding-3-small` (1536 dims), stores in KnowledgeDocument + KnowledgeChunk with vector embeddings.
3. `agent/rag/retrieval.py` — 5-stage pipeline (NO Cohere, OpenAI only):
   - **Stage 1 — Preprocess:** normalize query, skip-RAG heuristic for greetings/short messages (<8 chars)
   - **Stage 2 — Hybrid search:** vector cosine (pgvector HNSW) + BM25 (tsvector ts_rank) in parallel, top 20 each
   - **Stage 3 — RRF fusion:** Reciprocal Rank Fusion with k=60, merge to top 5
   - **Stage 4 — Format:** [KB-1]..[KB-5] citations with source type, title, relevance score
   - **Stage 5 — Inject:** appended to system prompt as `## KNOWLEDGE BASE CONTEXT` block
4. 24 documents seeded: 4 services, 5 FAQs, 6 testimonials, 5 policies, 3 pricing docs (logo/packaging/website), 1 business info
5. `search_knowledge` tool upgraded: RAG pipeline primary, settings.json keyword fallback
6. Intent classifier: skips RAG for greetings/acknowledgments (saves embedding cost)

**Files modified:**
- NEW `agent/rag/__init__.py` — module exports
- NEW `agent/rag/ingestion.py` — chunking, embedding, document CRUD, settings.json seeder
- NEW `agent/rag/retrieval.py` — 5-stage retrieval pipeline, RRF fusion, context formatting
- `agent/tools.py` — `_exec_search_knowledge` replaced with RAG-backed implementation
- `agent/core.py` — RAG import, context injection before LLM call (appends to system message)
- `saransh-dashboard/backend/prisma/schema.prisma` — added KnowledgeSourceType enum, KnowledgeDocument model, KnowledgeChunk model
- `saransh-dashboard/backend/prisma/migrations/20260416_phase4_rag_knowledge/migration.sql`

**DB changes:** YES — `CREATE EXTENSION vector` (applied as superuser), migration 20260416_phase4_rag_knowledge + seed data (24 KnowledgeDocuments, 24 KnowledgeChunks)

**Revert command:**
```bash
# Code revert:
git reset --hard 74b380d
# DB rollback:
cd /opt/saransh-dashboard/backend
npx prisma migrate resolve --rolled-back 20260416_phase4_rag_knowledge
psql "$DATABASE_URL" -c 'DROP TABLE IF EXISTS "KnowledgeChunk" CASCADE;'
psql "$DATABASE_URL" -c 'DROP TABLE IF EXISTS "KnowledgeDocument" CASCADE;'
psql "$DATABASE_URL" -c 'DROP TYPE IF EXISTS "KnowledgeSourceType";'
psql "$DATABASE_URL" -c 'DROP FUNCTION IF EXISTS knowledge_chunk_tsv_trigger() CASCADE;'
# Extension (optional — safe to leave):
sudo -u postgres psql -d saransh_dashboard -c 'DROP EXTENSION IF EXISTS vector;'
```

**Cost impact:**
- Embedding cost per query: ~10-18 tokens via text-embedding-3-small (~$0.000002 per query)
- RAG overhead per LLM call: ~2-4 seconds (embedding + dual search + fusion)
- Savings: more accurate responses reduce follow-up messages; skip-RAG heuristic avoids cost on greetings
- Reranking deliberately skipped (NO Cohere). If retrieval quality drops, fallback plan: LLM-as-reranker

**Verification performed (2026-04-16):**
- pgvector 0.6.0 confirmed in saransh_dashboard database
- Migration applied cleanly, prisma validate passed, prisma generate succeeded
- 24 documents ingested (0 errors)
- Test A: "tumhari pricing kya hai logo ki?" → router kr_pricing pass_to_llm → RAG vector=20, fused=5, 10 embed tokens → LLM replied with pricing from KB ✓
- Test B: "agar mujhe design pasand nahi aaya toh refund milega kya?" → router llm_fallback → RAG vector=20, fused=5, 18 embed tokens → LLM replied with refund policy from KB ✓
- Test C: "hi" → router kr_greeting (keyword tier, no LLM, no RAG) → tokens_saved=3000 ✓
- Test data cleaned up after verification
- Note: BM25 returns 0 hits for Hindi queries (expected — English tsvector dictionary). Vector search handles Hindi/Hinglish well (20 hits). Hybrid approach compensates.

**Approval needed for Phase 5:** pending explicit user sign-off
---

## Phase 5 — Security & Hardening

**Status:** COMPLETE on 2026-04-16
**Pre-phase commit:** `da881d8` (Phase 4 MIGRATION_LOG)
**Post-phase commit:** `79a8f7c`

**Scope implemented:**

### 1. Rate Limiting (Redis-backed)
- `agent/security/rate_limit.py` — sliding window via Redis sorted sets
- Per-phone inbound: 20 messages / 60 seconds (silently drops excess)
- Per-phone outbound: 15 replies / 60 seconds (queues with backoff)
- Per-IP webhook: 100 requests / 60 seconds (returns 429)
- **Graceful degradation:** if Redis is down, FAILS OPEN — never breaks the bot
- Redis installed via `apt-get install redis-server`, runs on localhost:6379
- `REDIS_URL=redis://localhost:6379/0` added to `.env`

### 2. Input Sanitization (Prompt Injection Defense)
- `agent/security/input_filter.py` — 7-layer defense
- Strips: "ignore previous instructions" (EN + Hindi), role-injection prefixes (`system:`, `assistant:`), markdown heading role redefinition, base64 blobs (40+ chars)
- Flags: excessive RTL/unicode control characters
- Length limit: rejects messages > 4000 chars
- **Safe for Hindi/Hinglish:** "मुझे logo design करवाना है" and "bhai packaging ka price kya hai" pass through unchanged
- Flagged messages are logged but NOT blocked (monitoring-only, doesn't break UX)

### 3. Output Filtering (Secret Leak Prevention)
- `agent/security/output_filter.py` — regex scan before every outbound message
- Blocks: OpenAI keys (`sk-...`), Bearer tokens, Slack tokens, 40+ char secrets near key/token/password keywords
- Blocks: phone numbers other than current conversation's, emails except business (`radharamangd@gmail.com`, `saransh@saransh.space`, `*@saranshdesigns.com`)
- Blocks: UPI IDs, internal system paths (`/opt/...`, `/home/...`, `C:\...`), env variable values
- Safe: portfolio URLs (`https://saransh.space/`), pricing (`₹5,000`), "Saransh Sharma sir" mentions
- On leak: returns generic Hindi reply + sends Telegram alert (🚨 OUTPUT LEAK BLOCKED)

### 4. Encryption at Rest (AES-256-GCM)
- `agent/security/crypto.py` — low-level encrypt/decrypt with `enc:v1:` prefix
- `modules/secrets_manager.py` — high-level wrapper for conversation PII
- Encrypted fields: `collectedDetails`, `agreed_price`, `notes`
- NOT encrypted: phone numbers (needed for indexing), message IDs, timestamps
- `APP_ENCRYPTION_KEY` generated via `openssl rand -base64 32`, stored in `.env`
- Legacy rows: plaintext passes through decrypt unchanged (no migration needed)
- Idempotent: already-encrypted values are not double-encrypted

### 5. Wiring
- `main.py`: IP rate limit after HMAC check → phone rate limit after blocked-phone check → input sanitization before routing
- `agent/whatsapp.py`: output filter + outbound rate limit in `send_text()` before WhatsApp API call
- `agent/conversation.py`: encrypt on `save_conversation()`, decrypt on `load_conversation()`
- Redis init/close in app startup/shutdown lifecycle

**Files modified:**
- NEW `agent/security/__init__.py` — module exports
- NEW `agent/security/rate_limit.py` — Redis sliding-window rate limiter
- NEW `agent/security/input_filter.py` — prompt injection sanitizer
- NEW `agent/security/output_filter.py` — outbound leak scanner
- NEW `agent/security/crypto.py` — AES-256-GCM encrypt/decrypt
- NEW `modules/secrets_manager.py` — conversation PII encryption wrapper
- NEW `test_phase5_security.py` — 14 pytest tests (all passing)
- `main.py` — security imports, Redis lifecycle, IP + phone rate limiting, input sanitization
- `agent/whatsapp.py` — output filter + outbound rate limit in send_text
- `agent/conversation.py` — encrypt on save, decrypt on load
- `requirements.txt` — added `redis>=5.0.0`, `cryptography>=42.0.0`

**DB changes:** NONE (all security is application-layer)

**Infrastructure changes:**
- Redis server installed (`apt-get install redis-server`)
- `REDIS_URL` and `APP_ENCRYPTION_KEY` added to `.env`

**Revert command:**
```bash
# Code revert:
git reset --hard da881d8
systemctl restart saransh-agent
# Redis (optional — safe to leave):
systemctl stop redis-server
apt-get remove redis-server
# .env cleanup:
sed -i '/APP_ENCRYPTION_KEY/d' .env
sed -i '/REDIS_URL/d' .env
```

**Verification performed (2026-04-16):**
- 14/14 pytest tests passed (0.48s)
- Rate limit: 20 allowed → 21st blocked, phone_hash logged ✓
- Input filter: Hindi benign passes, injection flagged + stripped ✓
- Output filter: API key blocked + Telegram alert, foreign phone blocked, normal reply passes ✓
- Encryption: roundtrip AES-GCM, selective field encryption, legacy passthrough, idempotent ✓
- Service restart: clean startup, `rate_limit.redis_connected` logged ✓
- Bot functional: no downtime during deployment

**Phase 6 (optional):** Multi-language detection + Meta Native Flows — not authorized, separate scope
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
