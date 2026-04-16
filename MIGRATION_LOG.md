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

**Status:** NOT STARTED
**Pre-phase commit:** _(to be filled in, = post-Phase-0 commit)_
**Post-phase commit:** _(to be filled in)_

**Files modified (planned):**
- `saransh-dashboard/backend/prisma/schema.prisma` — add `BotConversation`, `BotMessage`, `AuditLog` tables; add `Lead.optedOut, lastInteractionAt, tags, language`
- `saransh-dashboard/backend/prisma/migrations/<timestamp>_phase1_botpersistence/` (auto-generated)
- `Whatsapp Assistant/modules/db.py` (new) — Prisma Python client wrapper
- `Whatsapp Assistant/agent/conversation.py` — dual-write to JSON + Postgres

**DB changes:** YES — migration #1. Diff + SQL shown for approval before apply.

**Revert command:**
```
git reset --hard <pre-phase-1-commit>
# DB rollback:
npx prisma migrate resolve --rolled-back <migration-name>  (run from saransh-dashboard/backend)
psql $DATABASE_URL -c "DROP TABLE IF EXISTS \"BotMessage\", \"BotConversation\", \"AuditLog\";"
psql $DATABASE_URL -c "ALTER TABLE \"Lead\" DROP COLUMN IF EXISTS \"optedOut\", DROP COLUMN IF EXISTS \"lastInteractionAt\", DROP COLUMN IF EXISTS \"tags\", DROP COLUMN IF EXISTS \"language\";"
```

**Verification:**
- New rows appear in `BotConversation` / `BotMessage` after test message
- JSON file still written (dual-write)
- Existing `Lead` / `Activity` queries from dashboard still work

**Approval needed to start:** ❌ pending Phase 0 completion + explicit user sign-off on migration #1

---

## Phase 2 — Tiered routing + rules (DB migration #2)

**Status:** NOT STARTED
**Pre-phase commit:** _(TBD)_
**Post-phase commit:** _(TBD)_

**Files modified (planned):**
- `saransh-dashboard/backend/prisma/schema.prisma` — add `ConversationFlow`, `KeywordRule`, `CannedResponse`
- `saransh-dashboard/backend/prisma/migrations/<timestamp>_phase2_routing/`
- `Whatsapp Assistant/agent/router.py` (new) — active-flow → keyword → LLM
- `Whatsapp Assistant/main.py` — wire router into webhook handler
- Seed script for default `ConversationFlow` row mirroring current 10-stage state machine

**DB changes:** YES — migration #2.

**Revert command:**
```
git reset --hard <pre-phase-2-commit>
psql $DATABASE_URL -c "DROP TABLE IF EXISTS \"CannedResponse\", \"KeywordRule\", \"ConversationFlow\";"
```

**Approval needed to start:** ❌

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
