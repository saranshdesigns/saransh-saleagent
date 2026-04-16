# Dashboard UI Parity Audit

> Audit date: 2026-04-16
> Scope: READ ONLY — what the Next.js dashboard shows vs what the v1.2 backend now supports (Phases 0-5)

## Architecture Note

The dashboard frontend (`saransh-dashboard/frontend/`) talks to **two backends**:
1. **Express/Prisma backend** (`saransh-dashboard/backend/`) — Lead CRUD, pipeline, analytics, settings
2. **Bot FastAPI server** (`/opt/saransh-saleagent`, port 8000) — WhatsApp conversations, messages, knowledge base

`NEXT_PUBLIC_API_URL` points to the bot API for WhatsApp features. The Express backend handles CRM data.

---

## A) Lead Scoring

| Field | In Prisma Schema | In Express API | In Dashboard UI | Status |
|-------|-------------------|----------------|-----------------|--------|
| `leadScore` (0-100) | **NO** — field doesn't exist in schema | Not exposed | Not shown | **MISSING** |
| `optedOut` | YES (`@default(false)`) | Not exposed in leads route | Not shown | **HIDDEN** |
| `lastInteractionAt` | YES (`DateTime?`) | Not exposed | Not shown | **HIDDEN** |
| `tags` | YES (`String[]`) | YES — leads route returns it | YES — shown in lead detail | **WORKING** |
| `language` | YES (`String?`) | Not exposed | Not shown | **HIDDEN** |

**What's happening:** The bot computes `leadScore` via `compute_lead_score()` in `agent/tools.py` and writes it to `BotConversation.seriousnessScore` (JSON file + Postgres dual-write). But the Lead table in Prisma has no `leadScore` column, and the dashboard's `quality` field (HOT/WARM/COLD) is **manually set by Saransh**, not auto-computed from the bot's score.

**Gap:** The bot knows lead quality (computed), the dashboard shows lead quality (manual), and they don't talk to each other.

**Effort to fix:** **M** — Add `leadScore Int?` to Lead schema, add Express endpoint to read it, add score badge + sort-by-score to leads page. Wire bot's `capture_lead` tool to update `Lead.leadScore`.

---

## B) BotConversation + BotMessage

| Table | In Prisma Schema | Express Route | Dashboard Query | Status |
|-------|-------------------|---------------|-----------------|--------|
| `BotConversation` | YES | **NO** route | **NO** — dashboard reads JSON files via bot API | **UNUSED by dashboard** |
| `BotMessage` | YES | **NO** route | **NO** — messages come from bot's JSON API | **UNUSED by dashboard** |

**What's happening:** The dashboard's WhatsApp tab calls the bot's FastAPI endpoints:
- `GET /api/whatsapp/conversations` → reads JSON files from `data/conversations/`
- `GET /api/whatsapp/conversations/{phone}` → reads a single JSON file

The bot does dual-write to Postgres (`BotConversation`/`BotMessage`) via Phase 1, but the dashboard never reads from Postgres. It still reads the JSON files via the bot API.

**Gap:** Two copies of conversation data exist (JSON files + Postgres). Dashboard reads the less structured one (JSON). The Postgres tables have richer data (stage, seriousness score, handoff status).

**Effort to fix:** **M** — Add Express routes that query `BotConversation`/`BotMessage` with Prisma. Update frontend to use these instead of bot JSON API. Bonus: conversation data becomes available even if bot is down.

---

## C) AuditLog

| Aspect | Status |
|--------|--------|
| Table exists in Prisma | YES |
| Bot writes to it | YES — opt-in/opt-out, config changes |
| Express route | **NO** |
| Dashboard viewer | **NO** |

**Gap:** Audit events are logged to Postgres but invisible. No way to see "who changed what when" from the dashboard.

**Effort to fix:** **S** — Add `GET /api/audit-logs` Express route (simple `findMany` with pagination + date filter). Add a table component on settings or a new `/dashboard/audit` page.

---

## D) Hot Leads / READY_FOR_CALL Queue

| Aspect | Status |
|--------|--------|
| Quality badges (HOT/WARM/COLD) | YES — `lead-detail.tsx:128`, `lead-card.tsx:99` |
| Filter by quality | YES — leads page has quality filter |
| Dedicated "hot leads queue" | **NO** |
| Auto-quality from bot score | **NO** — quality is manually set |
| READY_FOR_CALL bucket | **NO** — only HOT/WARM/COLD exist in UI |

**What exists:** The leads page shows colored badges (HOT=red, WARM=orange, COLD=blue). You can filter by quality. The main dashboard page shows recent leads with quality badges.

**What's missing:** No dedicated "action queue" for hot leads — they're mixed into the general leads list. No auto-promotion based on the bot's lead score. The bot's `READY_FOR_CALL` bucket (score >= 86) doesn't exist in the dashboard vocabulary.

**Effort to fix:** **S** — Add a "Hot Leads" card on the main dashboard page that queries `Lead WHERE quality = 'HOT' ORDER BY updatedAt DESC LIMIT 10`. Wire bot score → Lead quality auto-update requires (M) work from item A.

---

## E) KeywordRule Management

| Aspect | Status |
|--------|--------|
| `KeywordRule` table | YES (Prisma schema) |
| Bot reads from it | YES — `agent/router.py` queries at runtime |
| Express route | **NO** |
| Dashboard UI | **NO** |

**Gap:** Keyword rules can only be managed via direct SQL (`psql`). No dashboard CRUD. The POST_MIGRATION_OPERATIONS_GUIDE has SQL insert patterns as a workaround.

**Effort to fix:** **M** — New Express CRUD routes + new section in settings page (table with keywords, match type, response, priority, enabled toggle). ~1 page of UI work.

---

## F) ConversationFlow Builder

| Aspect | Status |
|--------|--------|
| `ConversationFlow` table | YES (Prisma schema) |
| Bot reads from it | YES — `agent/router.py` |
| Express route | **NO** |
| Dashboard UI | **NO** |

**Gap:** Conversation flows can only be created via SQL. No visual builder.

**Effort to fix:** **L** — A flow builder with step-by-step configuration, conditional branching UI, and preview would be a significant frontend effort. A simpler "flow editor" (JSON steps in a form) would be **M**.

---

## G) Canned Responses

| Aspect | Status |
|--------|--------|
| `CannedResponse` table | YES (Prisma schema) |
| Express route | **NO** |
| Dashboard chat picker | **NO** |

**Gap:** When replying manually in the WhatsApp chat window, Saransh has to type every response from scratch. No shortcut picker for common replies.

**Effort to fix:** **S** — Add Express CRUD for canned responses. Add a `/` picker in the chat input (type `/` to search canned responses, click to insert). Common pattern, well-documented.

---

## H) Knowledge Base (RAG Tables vs settings.json)

| Aspect | Status |
|--------|--------|
| KB tab in dashboard | YES — WhatsApp page, "Knowledge Base" tab |
| What it reads | **settings.json** via bot API (`GET /api/settings/knowledge-base`) |
| What it writes | **settings.json** via bot API (`POST/PUT/DELETE /api/settings/knowledge-base/{id}`) |
| KnowledgeDocument table | EXISTS in Postgres (Phase 4) — **NOT used by dashboard** |
| KnowledgeChunk table | EXISTS in Postgres (Phase 4) — **NOT used by dashboard** |
| RAG embeddings | Bot ingests from settings.json → Postgres on startup, but dashboard edits only touch settings.json |

**Gap:** The dashboard KB editor and the RAG pipeline are disconnected:
1. Dashboard edits settings.json (Q&A pairs: question + answer)
2. Bot's RAG pipeline reads from KnowledgeDocument/KnowledgeChunk (Postgres with embeddings)
3. On bot startup, `ingest_documents_from_settings()` seeds Postgres from settings.json
4. But edits made in dashboard between restarts are only in settings.json — not in Postgres (RAG doesn't see them until next restart or manual re-seed)

**Effort to fix:** **M** — Two options:
- **Quick fix:** Add a webhook/trigger so that when dashboard saves a KB entry, it also calls a bot endpoint to re-ingest that document into Postgres. ~2 hours.
- **Proper fix:** Dashboard reads/writes KnowledgeDocument table directly via Express routes. Bot's RAG reads from Postgres only. Settings.json becomes a migration source, not the live store. ~1 day.

---

## I) Rate Limit / Security Metrics

| Aspect | Status |
|--------|--------|
| Rate limit stats | **NO** dashboard view |
| Input filter flags | **NO** dashboard view |
| Output filter blocks | **NO** dashboard view |
| Security settings page | EXISTS — but only has a password change form (`settings/page.tsx`) |

**Gap:** All security events are in structured logs only (`journalctl`). No dashboard visibility. The operations guide has CLI commands for monitoring, but no at-a-glance dashboard view.

**Effort to fix:** **M** — Add a "Security" tab on settings or analytics page. Bot would need new API endpoints that query Redis for rate limit stats and parse recent structured log entries. Alternatively, aggregate security events into a simple counter table.

---

## Summary Matrix

| Capability | DB | API | UI | Verdict |
|------------|----|----|-----|---------|
| A. Lead scoring | Partial | No | No | **GAP** |
| B. BotConversation/Message | Yes | No | No (uses JSON) | **GAP** |
| C. AuditLog | Yes | No | No | **GAP** |
| D. Hot leads queue | Yes | Yes | Partial (filter only) | **PARTIAL** |
| E. KeywordRule mgmt | Yes | No | No | **GAP** |
| F. ConversationFlow builder | Yes | No | No | **GAP** |
| G. Canned responses | Yes | No | No | **GAP** |
| H. Knowledge Base (RAG) | Yes | Yes (wrong source) | Yes (wrong source) | **MISALIGNED** |
| I. Security metrics | Logs only | No | No | **GAP** |

---

## TOP 5 UI Additions — Best Daily Value vs Effort

### 1. Canned Responses Picker in Chat — **Effort: S**
**Why #1:** You manually reply to leads daily. A `/` picker that inserts pre-written responses (pricing, process, portfolio links) would save 5-10 minutes per conversation. High frequency, low effort. Express CRUD + chat input component.

### 2. KB Sync to RAG Pipeline — **Effort: S-M**
**Why #2:** Right now, KB edits in the dashboard don't reach RAG until bot restart. A "sync" button or auto-webhook would ensure the bot's AI responses use your latest knowledge immediately. Critical for accuracy — you edit KB entries often.

### 3. Lead Score Display + Auto-Quality — **Effort: M**
**Why #3:** The bot already computes lead scores but they're invisible. Adding a score badge to the leads list + auto-setting quality (HOT/WARM/COLD) based on the bot's score means you stop guessing which leads are serious. Prioritize follow-ups by data, not gut feel.

### 4. AuditLog Viewer — **Effort: S**
**Why #4:** When something goes wrong at 2am, you need "what changed." A simple paginated table showing opt-ins/opt-outs, config changes, and escalations gives you instant visibility. One Express route + one table component.

### 5. Hot Leads Dashboard Card — **Effort: S**
**Why #5:** A "Needs Attention" card on the main dashboard showing leads with score >= 61 (or quality = HOT) sorted by recency. One glance tells you who to call today. No new page needed — just a card on the existing dashboard home.

### Honorable mentions (deferred):
- **KeywordRule management UI (M)** — useful but you rarely change rules
- **Security metrics dashboard (M)** — valuable but the operations guide CLI commands cover this for now
- **ConversationFlow builder (L)** — biggest effort, defer until you have flows complex enough to need visual editing
- **BotConversation as primary data source (M)** — correctness improvement but current JSON approach works fine

---

*This audit is read-only. No code was changed. Recommendations are ordered by daily value / effort ratio for a solo operator who manages leads via this dashboard every day.*
