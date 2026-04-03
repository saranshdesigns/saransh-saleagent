# SaranshDesigns AI Sales Agent v1.1 — Technical Blueprint

> Last Updated: 2026-04-01
> This document is the single source of truth for the entire system architecture.
> Used by orchestrators, developers, and future AI agents to plan changes.

---

## 1. Project Overview

**What it does:** An AI-powered WhatsApp sales agent for SaranshDesigns, a freelance branding studio run by Saransh Sharma. The agent handles the complete pre-sale cycle — from first contact to owner handoff — autonomously via WhatsApp.

**Core flow:**
1. Client messages the WhatsApp business number
2. AI agent identifies service needed (Logo / Packaging / Website)
3. Collects project details conversationally (one question at a time)
4. Presents pricing and handles negotiation within allowed limits
5. On price confirmation, triggers Owner handoff via WhatsApp alert
6. Owner takes over for payment collection and project execution

**Key capabilities:**
- Multilingual (English / Hindi / Hinglish — matches client's language)
- Time-based IST greetings (Good morning/afternoon/evening)
- Smart portfolio delivery from Google Drive with image pair rules
- Automated follow-ups (5-min, 6-hour, 24-hour)
- Real-time owner dashboard with WebSocket updates
- Owner can inject messages, update pricing, and monitor all conversations
- Seriousness scoring (0-100) to gauge lead quality

---

## 2. Architecture Overview

```
                    ┌──────────────────────┐
                    │  Meta WhatsApp Cloud  │
                    │   Business API v22    │
                    └─────────┬────────────┘
                              │ Webhook POST
                              ▼
                    ┌──────────────────────┐
                    │   nginx (HTTPS)      │
                    │  agent.saransh.space  │
                    │  Let's Encrypt SSL   │
                    └─────────┬────────────┘
                              │ reverse proxy → :8000
                              ▼
               ┌──────────────────────────────┐
               │     FastAPI + Uvicorn         │
               │         main.py               │
               │                               │
               │  ┌─────────┐  ┌────────────┐ │
               │  │ Webhook  │  │ Dashboard  │ │
               │  │ Handler  │  │ API + WS   │ │
               │  └────┬─────┘  └─────┬──────┘ │
               │       │              │         │
               │       ▼              ▼         │
               │  ┌──────────────────────────┐ │
               │  │    agent/core.py          │ │
               │  │  (OpenAI GPT-4o-mini)     │ │
               │  └────┬──────────┬───────────┘│
               │       │          │             │
               │       ▼          ▼             │
               │  ┌─────────┐ ┌──────────────┐ │
               │  │ conver-  │ │ whatsapp.py  │ │
               │  │sation.py │ │ (Meta API)   │ │
               │  └────┬─────┘ └──────────────┘ │
               │       │                         │
               │       ▼                         │
               │  data/conversations/*.json      │
               └──────────────────────────────────┘
                              │
               ┌──────────────┴──────────────┐
               │      Google Drive API       │
               │   (Service Account, R/O)    │
               │   Portfolio image delivery  │
               └─────────────────────────────┘
```

**Request lifecycle (client message):**
1. Meta sends webhook POST to `/webhook`
2. `main.py` parses payload, identifies sender phone
3. If sender is `OWNER_PHONE` → routed to `process_owner_command()`
4. If client → `handle_client_message()` is called as async task
5. Special handlers checked first: greeting, call request, portfolio/sample request
6. Standard path: `core.py → process_message()` calls OpenAI
7. Reply sent via `whatsapp.py → send_text()`
8. Stage/details auto-extracted and persisted to JSON
9. Follow-up timers scheduled via APScheduler
10. Dashboard notified via WebSocket broadcast

---

## 3. Tech Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| **Language** | Python | 3.11+ |
| **Web Framework** | FastAPI | 0.111.0 |
| **ASGI Server** | Uvicorn | 0.30.1 |
| **AI Engine** | OpenAI API | 1.35.0 |
| **AI Models** | gpt-4o-mini (text), gpt-4o (vision) | — |
| **WhatsApp** | Meta WhatsApp Business Cloud API | v22.0 |
| **HTTP Client** | httpx | 0.27.0 |
| **Portfolio Storage** | Google Drive API | v3 |
| **Google Auth** | google-api-python-client | 2.131.0 |
| **Scheduler** | APScheduler (AsyncIO) | 3.10.4 |
| **Auth (Dashboard)** | python-jose (JWT) + passlib (bcrypt) | 3.3.0 / 1.7.4 |
| **Image Processing** | Pillow | 10.4.0 |
| **Dashboard Frontend** | Vanilla JS + HTML + CSS (no build step) | — |
| **Server OS** | Ubuntu 24.04 (DigitalOcean) | — |
| **Process Manager** | systemd | — |
| **Reverse Proxy** | nginx + Let's Encrypt | — |
| **Storage** | JSON files (no database) | — |
| **Deployment** | GitHub + SSH pull + systemctl restart | — |

---

## 4. Folder & File Structure

```
AI Agent 1.0 - Cloude Code/
│
├── main.py                          # FastAPI app — webhook, dashboard API, scheduler, WebSocket
├── test_agent.py                    # Local conversation simulator (no WhatsApp)
├── requirements.txt                 # Python dependencies (15 packages)
├── deploy.sh                        # Production deploy: git push → SSH pull → systemctl restart
├── server_setup.sh                  # One-time DigitalOcean setup (packages, venv, firewall, systemd)
├── saransh-agent.service            # systemd unit file
├── setup.bat                        # Local dev: create venv + install deps (Windows)
├── start.bat                        # Local dev: run the agent (Windows)
├── ngrok_start.bat                  # Local dev: expose :8000 via ngrok for Meta webhook
├── .env                             # Environment secrets (NOT in git)
├── .env.example                     # Template with all required env vars
├── .gitignore                       # Excludes venv, .env, __pycache__, data/
│
├── agent/                           # Core Python modules
│   ├── __init__.py                  # Package init
│   ├── core.py                      # AI brain — system prompt, OpenAI calls, intent detection,
│   │                                #   stage detection, detail extraction, owner commands
│   ├── conversation.py              # State manager — load/save JSON per phone, stage transitions,
│   │                                #   message history, seriousness scoring, multi-project support
│   ├── whatsapp.py                  # Meta API client — send text/image, owner alerts, media download
│   ├── portfolio.py                 # Portfolio router — Google Drive (primary) → local folder (fallback)
│   ├── drive_portfolio.py           # Google Drive integration — 3-level folder traversal, image caching,
│   │                                #   category matching, pair rule (1.1 + 1.2)
│   └── dashboard_auth.py           # JWT auth — password verify, token create/decode, FastAPI dependency
│
├── config/                          # Runtime configuration (editable, immediate effect)
│   ├── pricing.json                 # All service pricing with min prices and negotiation limits
│   └── settings.json                # Agent personality, portfolio links, learned behaviors, blocked cats
│
├── dashboard/                       # Owner dashboard (static SPA)
│   ├── index.html                   # Login + main layout
│   ├── app.js                       # Vanilla JS — fetch API, WebSocket, real-time updates
│   └── style.css                    # Full CSS (dark theme)
│
├── data/                            # Runtime data (NOT in git)
│   ├── conversations/               # One JSON file per client phone number
│   │   ├── 918651123458.json
│   │   ├── 917897925936.json
│   │   └── ...
│   └── portfolio_cache/             # Downloaded Google Drive images (keyed by file_id)
│
├── credentials/                     # Service account key (NOT in git)
│   └── google_service_account.json
│
└── modules/                         # Reserved for future modules (currently empty)
    └── __init__.py
```

---

## 5. Data Flow

### 5.1 Client Message → AI Reply

```
Client WhatsApp Message
        │
        ▼
Meta Cloud API → POST /webhook (JSON payload)
        │
        ▼
main.py:receive_message()
  ├── Parse: entry[0].changes[0].value.messages[0]
  ├── Extract: phone, msg_type (text/image), body
  ├── Owner check: if phone == OWNER_PHONE → process_owner_command()
  └── Client: asyncio.create_task(handle_client_message())
              │
              ▼
        handle_client_message()
          ├── Cancel existing follow-up timers for this phone
          ├── Image? → download_media() → encode_image_to_base64()
          ├── Context revival: check if HANDOFF/CLOSED (24h expiry logic)
          ├── Special handlers (short-circuit):
          │   ├── Greeting (hi/hello) → hardcoded IST greeting + service intro
          │   ├── Call request ("call me") → send_owner_alert() immediately
          │   └── Portfolio request ("show samples") → handle_portfolio_request()
          └── Standard AI path:
                │
                ▼
          core.py:process_message(phone, text, image_data)
            ├── load_conversation(phone)
            ├── add_message(phone, "user", text)
            ├── detect_intent(text) → cheap gpt-4o-mini call → {service, intent, urgency}
            ├── update_service() if detected
            ├── build_messages_for_openai() → system_prompt + pricing + state + history (15 msgs)
            ├── OpenAI chat.completions.create() → gpt-4o-mini (or gpt-4o for images)
            ├── Hardcode IST greeting if first message
            ├── add_message(phone, "assistant", reply)
            ├── _update_stage_from_reply() → auto-detect stage transitions
            └── _extract_and_store_details() → silent gpt-4o-mini call to parse structured data
                │
                ▼
          Reply sent: whatsapp.py:send_text(phone, reply)
          Dashboard notified: ws_manager.broadcast({type: "new_message", ...})
          Handoff check: if reply contains trigger phrases → send_owner_alert()
          Schedule: 5-min quick follow-up + 6-hour follow-up
```

### 5.2 Portfolio Request Flow

```
Client: "show me samples"
        │
        ▼
handle_portfolio_request(phone, text)
  ├── Detect service from current message (or fall back to stored service)
  ├── Extract packaging_type from stored details or conversation
  ├── Extract category from stored details or conversation
  │
  ▼
portfolio.py:get_samples(service, category, packaging_type)
  ├── Check: drive_portfolio.drive_available()?
  │   YES → drive_portfolio.get_drive_samples()
  │          ├── Build Google Drive service (service account)
  │          ├── Navigate: root folder → service folder → packaging type → category
  │          ├── Match: exact category → fuzzy match → parent fallback → mixed samples
  │          ├── Apply pair rule: "Brand 1.1" + "Brand 1.2" always together
  │          ├── Download to data/portfolio_cache/ (skip if cached)
  │          └── Return: {found: true, files: [Path, ...], message: "..."}
  │   NO → Local folder fallback (same logic with local paths)
  │
  ▼
Send to client:
  ├── send_text(phone, intro_message)
  ├── for each file: send_image(phone, path) — max 10 images
  ├── send_text(phone, follow_up + PORTFOLIO_LINKS)
  └── Schedule: 5-min portfolio follow-up
```

### 5.3 Owner Alert Flow

```
Trigger conditions:
  1. AI reply contains handoff phrases ("connect you with Saransh Sharma sir", "He will message you shortly", etc.)
  2. Client says "call me" / "baat karni hai"
  3. Client confirms variant negotiation → escalate to Saransh Sir

        │
        ▼
whatsapp.py:send_owner_alert(summary)
  ├── Build formatted alert message:
  │   - Service, Client phone, Stage, Seriousness Score
  │   - Collected details, Agreed price, Images count
  │   - Multi-project summary (if any)
  │   - Existing logo flag (if redesign)
  │   - Notes, Action required
  ├── send_text(OWNER_PHONE, alert_message)
  └── Log API response for debugging
```

### 5.4 Follow-Up Timer Chain

```
After every agent reply (non-handoff):
  ├── 5 min: Quick follow-up — "Please let me know your requirements..."
  │          (only if client hasn't replied since agent's last message)
  │
  ├── 5 min (portfolio-specific): "Did you get a chance to check the samples?"
  │          (only after portfolio send, only if no client reply)
  │
  ├── 6 hours: First follow-up — "Just following up — still interested?"
  │            → Then schedules 24h final
  │
  └── 24 hours: Final follow-up — "This is our last follow-up..."
               → Stage set to CLOSED

All timers cancelled when:
  - Client sends any new message
  - Handoff is triggered
  - Conversation is reset from dashboard
```

---

## 6. API Integrations

### 6.1 Meta WhatsApp Business API (v22.0)

| Operation | Endpoint | Usage |
|-----------|----------|-------|
| **Send text** | `POST /v22.0/{PHONE_NUMBER_ID}/messages` | All text messages to clients and owner |
| **Send image** | Upload: `POST /v19.0/{PHONE_NUMBER_ID}/media` → Send with media_id | Portfolio samples |
| **Download media** | `GET /v19.0/{media_id}` → get URL → download | Client-sent images |
| **Webhook verify** | `GET /webhook?hub.verify_token=...` | Meta initial setup |
| **Webhook receive** | `POST /webhook` | All incoming messages |

**Auth:** Bearer token in Authorization header (`META_WHATSAPP_TOKEN`)

### 6.2 OpenAI API

| Call | Model | Purpose | Max Tokens |
|------|-------|---------|------------|
| **Main response** | gpt-4o-mini | Generate conversational reply | 600 |
| **Vision response** | gpt-4o | Analyze client-sent images | 600 |
| **Intent detection** | gpt-4o-mini | Quick routing: service + intent + urgency | 100 |
| **Detail extraction** | gpt-4o-mini | Parse structured fields from conversation | 200 |
| **Price update parse** | gpt-4o-mini | Parse owner price-change commands | 100 |

**Per message: 2-3 API calls** (main response + intent detection + detail extraction)

### 6.3 Google Drive API (v3)

| Operation | Usage |
|-----------|-------|
| `files.list()` | Browse portfolio folders (3-level deep) |
| `files.get_media()` | Download portfolio images to local cache |

**Auth:** Service account (`credentials/google_service_account.json`), read-only scope
**Folder structure:** `Portfolio/ → Logo|Packaging|Website/ → Type/ → Category/`

---

## 7. Environment Variables

### Required (agent won't function without these)

| Key | Description |
|-----|-------------|
| `OPENAI_API_KEY` | OpenAI API key for GPT-4o-mini / GPT-4o |
| `META_PHONE_NUMBER_ID` | WhatsApp Business phone number ID |
| `META_WHATSAPP_TOKEN` | Meta Graph API access token (long-lived) |
| `META_VERIFY_TOKEN` | Webhook verification token (default: `saranshdesigns_webhook_2024`) |
| `OWNER_PHONE` | Owner's WhatsApp number, no `+` (e.g., `918850069662`) |
| `DASHBOARD_PASSWORD` | Dashboard login password (plain text or bcrypt hash) |
| `DASHBOARD_SECRET_KEY` | JWT signing key (32+ random chars) |

### Optional

| Key | Default | Description |
|-----|---------|-------------|
| `GOOGLE_CREDENTIALS_PATH` | `credentials/google_service_account.json` | Path to service account key |
| `GOOGLE_DRIVE_FOLDER_ID` | (empty) | Root portfolio folder ID in Google Drive |
| `PORTFOLIO_PATH` | `E:\Drive\SaranshDesigns\Portfolio` | Local portfolio fallback path |
| `APP_PORT` | `8000` | Server port |
| `DEBUG` | `false` | Debug mode |

---

## 8. Key Functions & Modules

### 8.1 main.py (FastAPI Server — 800+ lines)

| Function | Line | Description |
|----------|------|-------------|
| `receive_message()` | ~114 | POST /webhook — entry point for all WhatsApp messages |
| `handle_client_message()` | ~236 | Async handler: greeting check, call/portfolio/AI routing |
| `handle_portfolio_request()` | ~453 | Fetches samples from Drive/local, sends images + links |
| `trigger_handoff()` | ~505 | Marks handoff, notifies client + owner |
| `_schedule_quick_followup()` | ~183 | 5-min general follow-up after any agent reply |
| `_schedule_portfolio_followup()` | ~207 | 5-min follow-up after portfolio send |
| `_schedule_followup()` | ~162 | 6h/24h follow-up chain |
| `_cancel_followups()` | ~174 | Cancel all pending timers for a phone |
| `_send_quick_followup()` | ~194 | Sends "Please share your requirements" nudge |
| `_send_portfolio_followup()` | ~218 | Sends "Did you check the samples?" nudge |
| `_send_first_followup()` | ~233 | 6h: "Still interested?" |
| `_send_final_followup()` | ~247 | 24h: "Last follow-up" → mark CLOSED |
| `_build_analytics()` | ~593 | Scan all JSON files for dashboard stats |
| `list_conversations()` | ~649 | GET /api/conversations — sorted summary list |
| `owner_send_message()` | ~742 | POST — owner sends WhatsApp msg from dashboard |
| `websocket_endpoint()` | ~776 | WebSocket with JWT auth for real-time updates |
| `_extract_category_from_text()` | ~524 | Maps product keywords → portfolio category |
| `_extract_packaging_type_from_text()` | ~549 | Maps keywords → pouch/box/label/sachet/jar |

### 8.2 agent/core.py (AI Brain — 789 lines)

| Function | Line | Description |
|----------|------|-------------|
| `SYSTEM_PROMPT` | ~43 | 400+ line mega-prompt: all business rules, flows, pricing, objection handling |
| `build_messages_for_openai()` | ~422 | Assembles system prompt + pricing + state + 15-msg history + current message |
| `process_message()` | ~557 | Main entry: intent detect → OpenAI call → stage update → detail extraction |
| `detect_intent()` | ~520 | Cheap gpt-4o-mini call → {service, intent, urgency} |
| `_get_ist_greeting()` | ~546 | Returns "Good morning/afternoon/evening!" based on IST hour |
| `_update_stage_from_reply()` | ~610 | Auto-detect: handoff phrases, pricing presented, agreement/rejection keywords |
| `_extract_and_store_details()` | ~642 | Silent gpt-4o-mini call → structured JSON → save to collected_details |
| `process_owner_command()` | ~708 | Parse owner WhatsApp commands: price update, reply style, block category |
| `_handle_price_update()` | ~740 | gpt-4o-mini parses command → updates pricing.json |

### 8.3 agent/conversation.py (State Manager — 211 lines)

| Function | Description |
|----------|-------------|
| `load_conversation(phone)` | Load JSON from disk (or create new) |
| `save_conversation(phone, data)` | Persist with IST timestamp |
| `add_message(phone, role, content)` | Append message, cap at 30 |
| `update_stage(phone, stage)` | Transition conversation stage |
| `update_details(phone, key, value)` | Add to collected_details dict |
| `update_seriousness(phone, delta)` | Adjust score (clamped 0-100) |
| `add_image(phone, url, caption, tag)` | Track received images with tags |
| `add_project(phone, service)` | Add multi-project entry |
| `mark_handoff(phone, agreed_price)` | Set stage=HANDOFF, save price |
| `get_summary(phone)` | Clean summary for owner alerts |
| `reset_conversation(phone)` | Delete JSON file |

**ConversationStage enum:** NEW → IDENTIFYING_SERVICE → COLLECTING_DETAILS → CONFIRMING_DETAILS → PRESENTING_PRICING → HANDLING_OBJECTION → NEGOTIATING → PRICING_CONFIRMED → HANDOFF → ESCALATED → CLOSED

**ServiceType enum:** LOGO, PACKAGING, WEBSITE, UNKNOWN

### 8.4 agent/whatsapp.py (Meta API Client — 171 lines)

| Function | Description |
|----------|-------------|
| `send_text(to, message)` | Send plain text via Meta API |
| `send_image(to, image_path, caption)` | Upload media then send |
| `send_portfolio_samples(to, paths, intro)` | Send up to 10 images sequentially |
| `send_owner_alert(summary)` | Formatted alert to OWNER_PHONE with full details |
| `send_escalation_alert(phone, question, service)` | Escalation alert to OWNER_PHONE |
| `download_media(media_id)` | Download client-sent image from Meta |
| `encode_image_to_base64(image_bytes)` | Convert for OpenAI vision |

### 8.5 agent/portfolio.py (Portfolio Router — ~200 lines)

| Function | Description |
|----------|-------------|
| `get_samples(service, category, packaging_type)` | Main entry: Drive → local fallback |
| `_drive_active()` | Check if Google Drive is configured and accessible |
| `_get_pairs(files)` | Group "1.1" + "1.2" files as pairs |
| `_flatten_pairs(pairs)` | Flatten pair groups into ordered list |
| `_get_mixed_samples(folder)` | 2 pairs from root + 2 pairs from each subfolder |

### 8.6 agent/drive_portfolio.py (Google Drive Integration — ~390 lines)

| Function | Description |
|----------|-------------|
| `drive_available()` | Check credentials + folder ID exist |
| `get_drive_samples(service, category, packaging_type)` | Main entry: navigate Drive folders |
| `_build_service()` | Build Google API service client |
| `_list_subfolders(parent_id)` | List immediate child folders |
| `_list_images(folder_id)` | List image files in a folder |
| `_find_matching_folder(folders, target)` | Fuzzy name match for categories |
| `_download_to_cache(file_id, name)` | Download image to data/portfolio_cache/ |
| `_apply_pair_rule(files)` | Ensure 1.1 + 1.2 always sent together |
| `_collect_mixed(folder_id)` | Root images + category subfolder samples |

### 8.7 agent/dashboard_auth.py (Auth — 50 lines)

| Function | Description |
|----------|-------------|
| `verify_password(plain)` | Check against .env (supports plaintext + bcrypt) |
| `create_access_token(data)` | Generate 12-hour JWT |
| `decode_token(token)` | Validate + decode JWT |
| `require_auth(authorization)` | FastAPI Depends() — validates Bearer token |

---

## 9. Database / Storage

**No database.** All data is stored as flat JSON files.

### 9.1 Conversation Files (`data/conversations/{phone}.json`)

One file per client phone number. Created on first message. Schema:

```json
{
  "phone": "918651123458",
  "stage": "collecting_details",
  "service": "packaging",
  "created_at": "2026-03-05T18:42:00+05:30",
  "last_updated": "2026-03-05T18:52:43+05:30",
  "messages": [
    {
      "role": "user|assistant|owner",
      "content": "...",
      "timestamp": "2026-03-05T18:42:00+05:30",
      "image_url": "[image]"
    }
  ],
  "collected_details": {
    "brand_name": "Aryan Masala",
    "category": "spices",
    "packaging_type": "pouch",
    "product_name": "Masala",
    "size_weight": "50g, 100g",
    "logo_available": false
  },
  "seriousness_score": 32,
  "images_received": [
    {
      "url": "...",
      "caption": "",
      "tag": "reference|existing_logo|sample_request",
      "timestamp": "..."
    }
  ],
  "agreed_price": null,
  "negotiation_count": 0,
  "handoff_triggered": false,
  "escalated": false,
  "cross_sell_opportunities": [],
  "notes": [],
  "projects": [],
  "active_project": 0
}
```

**Message cap:** 30 messages retained in memory. Older messages pruned on save.
**All timestamps:** IST (Asia/Kolkata, UTC+05:30)

### 9.2 Portfolio Cache (`data/portfolio_cache/`)

Downloaded Google Drive images, named by Drive file ID. Prevents re-downloading on subsequent requests.

### 9.3 Config Files (version controlled)

- `config/pricing.json` — Live pricing, editable by owner via WhatsApp command or direct edit. Changes take effect immediately (loaded on every request).
- `config/settings.json` — Agent name, models, portfolio links, learned behaviors, blocked categories.

---

## 10. Dashboard

### Overview

Single-page vanilla JS app served from `/dashboard/`. No build step, no framework.

| File | Purpose |
|------|---------|
| `index.html` | Login form + main layout (sidebar + chat area + analytics bar) |
| `app.js` | All logic: API calls, WebSocket, real-time updates, IST time formatting |
| `style.css` | Dark theme, responsive layout |

### Features

- **Login:** Password → JWT (12h expiry) stored in localStorage
- **Conversation list:** Sorted by handoff (first) then last_updated. Shows phone, stage badge, score, last message preview
- **Chat view:** Full message history with timestamps, role badges (Client/AI/Owner)
- **Owner messaging:** Type and send messages directly from dashboard (saved as role="owner", translated to "assistant" for OpenAI context)
- **Message deletion:** Remove wrong AI messages from context so AI won't reference them
- **Conversation reset:** Full reset for testing or fresh start
- **Analytics bar:** Total conversations, active today, handoffs count, stage breakdown
- **Real-time clock:** IST with "IST" label
- **WebSocket:** Auto-reconnect, receives new_message / owner_message_sent / conversation_reset / message_deleted events

### API Endpoints (all require Bearer JWT)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/login` | `{password: "..."}` → `{access_token: "..."}` |
| GET | `/api/analytics` | Dashboard stats summary |
| GET | `/api/conversations` | List all conversations (sorted) |
| GET | `/api/conversations/{phone}` | Full conversation with messages |
| POST | `/api/conversations/{phone}/send` | Owner sends message to client |
| DELETE | `/api/conversations/{phone}` | Reset conversation |
| DELETE | `/api/conversations/{phone}/messages/{idx}` | Delete single message |
| WS | `/ws` | Real-time dashboard feed (JWT as first frame) |
| GET | `/health` | `{status: "ok"}` |

---

## 11. Deployment Details

### Production Server

| Detail | Value |
|--------|-------|
| **Provider** | DigitalOcean |
| **Droplet IP** | 165.232.178.128 |
| **Region** | BLR1 (Bangalore) |
| **OS** | Ubuntu 24.04 |
| **Specs** | 1 vCPU, 1 GB RAM |
| **Domain** | agent.saransh.space |
| **HTTPS** | nginx + Let's Encrypt (certbot) |
| **App path** | /opt/saransh-saleagent |
| **Process** | systemd service: `saransh-agent` |
| **Port** | 8000 (internal), 443 (external via nginx) |
| **Auto-restart** | Yes (Restart=always, RestartSec=5) |
| **Logs** | `journalctl -u saransh-agent -f` |
| **GitHub repo** | github.com/saranshdesigns/saransh-saleagent |

### Deploy Workflow (`bash deploy.sh`)

```
1. git add -A && git commit -m "deploy: YYYY-MM-DD HH:MM"
2. git push origin main
3. SSH into droplet:
   - cd /opt/saransh-saleagent
   - git pull origin main
   - pip install -r requirements.txt -q
   - systemctl restart saransh-agent
4. Verify: systemctl is-active saransh-agent
```

**Total deploy time:** ~15 seconds

### First-Time Setup (`bash server_setup.sh`)

1. Install Python 3.11, git, ufw
2. Clone repo to /opt/saransh-saleagent
3. Create venv + install dependencies
4. Create data/ and credentials/ directories
5. Configure firewall (SSH + port 8000)
6. Install systemd service

Then manually: upload .env + Google credentials + start service

---

## 12. Pricing Configuration

### Logo Design

| Package | Base Price | Min Price | Negotiation Steps |
|---------|-----------|-----------|-------------------|
| Logo Package | ₹2,999 | ₹2,500 | 2800 → 2600 → 2500 |
| Total Branding | ₹4,999 | ₹4,500 | — |

**Includes:** 3 concepts, primary + secondary + submark + favicon, 5 revisions, PNG/JPEG/PDF/SVG/AI files

### Packaging Design

| Type | Master | Min | Variant | Min | Size Change |
|------|--------|-----|---------|-----|-------------|
| Pouch/Box | ₹5,000 | ₹4,000 | ₹2,000 | ₹1,000 | ₹500 (fixed) |
| Label | ₹3,000 | ₹2,500 | ₹1,000 | ₹600 | ₹400 (fixed) |

**Size change is non-negotiable.** Advance: 50%

### Website Design

| Package | Price | Token Advance |
|---------|-------|---------------|
| Starter (5 pages) | ₹6,999 | ₹2,000 (min ₹1,000) |
| Business (8-10 pages) | ₹11,999 | ₹2,000 (min ₹1,000) |

### Extras

| Item | Price |
|------|-------|
| Urgent delivery (2-3 days) | +₹500 (all services, non-negotiable) |
| Extra logo concept | ₹1,000 per concept |
| Extra packaging concept (Pouch/Box) | ₹1,500 per concept |
| Extra packaging concept (Label) | ₹1,000 per concept |

---

## 13. Business Rules Summary

| Rule | Detail |
|------|--------|
| **Language matching** | Reply in client's language (English/Hindi/Hinglish). Mandatory. |
| **WE language** | Always "we" not "I" when referring to services/work |
| **Owner name** | Always "Saransh Sharma sir" — never "ji", never "Saransh Sir" alone |
| **Greeting** | IST time-based, mandatory on first message |
| **Question flow** | 1-2 questions per message, never numbered lists |
| **Packaging intake** | Only ask: product category, type, product name/size/variants, brand name |
| **No upfront FSSAI/MRP** | Those details collected by owner after handoff |
| **Portfolio pair rule** | "Brand 1.1" + "Brand 1.2" always sent together |
| **Max samples** | 10 images per send |
| **Multi-project** | Only if client explicitly mentions multiple projects |
| **Handoff** | Only after price confirmation — never volunteer it early |
| **Payment** | Never collect — always hand off to owner |
| **Discount** | Gradual steps only (3 pushbacks max), never jump to minimum |
| **Variant objection** | Don't reduce — escalate to Saransh Sir |
| **Cross-sell** | Packaging → pitch logo if missing; Logo → pitch packaging if needed |
| **Seriousness 65+** | Negotiation flexibility allowed |
| **Follow-up chain** | 5min → 6h → 24h → CLOSED |

---

## 14. Current Known Issues & TODOs

### Known Issues

| # | Issue | Severity | Details |
|---|-------|----------|---------|
| 1 | ~~No webhook signature verification~~ | ✅ RESOLVED 2026-04-01 | HMAC-SHA256 verification of `X-Hub-Signature-256` header using `META_APP_SECRET`. Rejects HTTP 403 on mismatch. |
| 2 | ~~No rate limiting~~ | ✅ RESOLVED 2026-04-01 | slowapi rate limiting: webhook 60/min, login 10/min, API 30/min per IP. Returns HTTP 429 on excess. |
| 3 | **No input sanitization** | MEDIUM | Client messages passed directly to OpenAI without sanitization. |
| 4 | **Handoff detection fragile** | MEDIUM | Relies on AI using exact trigger phrases — can miss if AI paraphrases. |
| 5 | ~~No message deduplication~~ | ✅ RESOLVED 2026-04-01 | In-memory wamid deduplication via `deque(maxlen=500)` + set. Duplicate webhooks return HTTP 200 silently. |
| 6 | **Scheduler not persistent** | LOW | APScheduler jobs lost on restart — pending follow-ups forgotten. |
| 7 | **JSON file locking** | LOW | Concurrent reads/writes to same conversation file could corrupt data. |
| 8 | **30-message cap** | LOW | Old messages pruned — long conversations lose early context. |

### Pending Enhancements

| # | Enhancement | Priority |
|---|-------------|----------|
| 1 | Persistent scheduler (Redis/DB-backed APScheduler) | MEDIUM |
| ~~2~~ | ~~Webhook signature verification (HMAC-SHA256)~~ | ✅ DONE |
| ~~3~~ | ~~Rate limiting (fastapi-limiter or similar)~~ | ✅ DONE |
| 4 | SQLite or PostgreSQL instead of JSON files | MEDIUM |
| ~~5~~ | ~~Message deduplication (track wamid)~~ | ✅ DONE |
| 6 | Conversation analytics (conversion rate, avg response time) | LOW |
| 7 | CRM integration (push leads to Google Sheets or Notion) | LOW |
| 8 | Voice message support (speech-to-text) | LOW |
| 9 | ~~Template messages for owner alerts (bypass 24h window)~~ | 🔄 IN PROGRESS — Code deployed. Template `owner_alert_handoff` must be approved in Meta Business Manager (24-48h). Once approved, 24h window fully bypassed. |

### Changelog

| Date | Change |
|------|--------|
| 2026-04-01 | Security fixes: webhook signature verification, rate limiting, message deduplication |
| 2026-04-02 | Website service package fully updated to v3 — new pricing, packages, ecommerce clarification, Shopify-only rule, seriousness detection, updated system prompt and pricing.json |
| 2026-04-03 | Website portfolio links added to system prompt (5 live sites) with detail-handling rules |
| 2026-04-03 | Telegram parallel alert system added (`agent/telegram_alert.py`) — owner alerts now fire on both WhatsApp and Telegram |

---

## 15. System Prompt Architecture

The system prompt in `core.py` is the heart of agent behavior. It's ~420 lines and structured as:

```
1. Identity & Role (WHO YOU ARE)
2. Language Rules (MANDATORY Hinglish/Hindi/English matching)
3. WE Language (never "I")
4. Services Offered (Logo, Packaging, Website)
5. Critical Conversation Flow (7-step sales funnel)
6. Time-Based Greeting (IST)
7. Multiple Projects (only when client says so)
8. Existing Logo Improvement
9. Logo Intake Flow (4 steps)
10. Logo Package Deliverables
11. Packaging Package Deliverables
12. Packaging Intake Flow (4 steps — simplified)
13. Packaging Type Rules & Smart Inference
14. Master vs Variant vs Size Change Definitions
15. Cylinder Printing Info
16. Pricing Calculation Rules
17. Delivery Timeline
18. Urgent Request (₹500 charge)
19. Variant Negotiation Escalation
20. Pricing Objection Handling
21. Website Required Details
22. Pricing & Negotiation Gradual Steps
23. Seriousness Scoring Rules
24. Owner Handoff Timing Rules
25. Escalation Triggers
26. Image Handling
27. Cross-Sell Rules
28. Portfolio/Samples Instructions
29. Editable Files Response
30. Time-Waster Handling
31. Meta Ads Lead Handling
32. AI Identity Response
33. Question Flow (conversational, never numbered lists)
34. Refund Policy
35. Extra Concept Charges
36. Tone Guidelines
37. Owner Name Rule

DYNAMIC CONTEXT INJECTED AT RUNTIME:
- Current live pricing from pricing.json
- Current IST time + period
- Conversation state (stage, service, details, score, images, projects)
- Is First Message flag
```

---

## 16. OpenAI Token Budget (per message)

| Component | Estimated Tokens |
|-----------|-----------------|
| System prompt (base) | ~2,500 |
| Pricing context | ~200 |
| Conversation state | ~150 |
| Message history (15 msgs) | ~1,500 |
| New message | ~50 |
| **Total input** | **~4,400** |
| Output (max) | 600 |
| Intent detection (separate call) | ~200 in + 100 out |
| Detail extraction (separate call) | ~1,200 in + 200 out |
| **Grand total per message** | **~6,700 tokens** |

At gpt-4o-mini pricing ($0.15/1M input, $0.60/1M output):
**~$0.001 per message** (~₹0.08)

---

*End of Blueprint*
