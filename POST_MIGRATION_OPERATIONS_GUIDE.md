# Post-Migration Operations Guide

> The 2am doc. Everything you need to monitor, manage, and troubleshoot the SaranshDesigns WhatsApp sales bot after the v1.2 migration (Phases 0–5).

**Server:** `ssh root@165.232.178.128`
**Service:** `systemctl status saransh-agent`
**Logs:** `journalctl -u saransh-agent -f` (structured JSON via structlog)
**DB:** `psql "$DATABASE_URL"` (source `.env` first)
**Redis:** `redis-cli`

---

## Monitoring Commands

### Check tier distribution (which routing tier handles messages)

```bash
# Last 1000 messages — how many went keyword vs LLM
journalctl -u saransh-agent --since "1 hour ago" --no-pager | \
  grep '"event": "client.llm' | grep -o '"route_tier":"[^"]*"' | sort | uniq -c | sort -rn

# Quick breakdown: keyword hits vs LLM fallback
journalctl -u saransh-agent --since "24 hours ago" --no-pager | \
  grep 'route_tier' | grep -oP '"route_tier":"(keyword|llm)"' | sort | uniq -c
```

Expected: ~40-60% keyword, ~40-60% LLM. If LLM is >80%, keyword rules may need updating.

### Check rate limiting hits

```bash
# Any rate limit triggers (should be rare for legit users)
journalctl -u saransh-agent --since "24 hours ago" --no-pager | grep 'rate_limit.hit'

# Count by bucket
journalctl -u saransh-agent --since "24 hours ago" --no-pager | \
  grep 'rate_limit.hit' | grep -oP '"bucket":"[^"]*"' | sort | uniq -c

# Redis live stats
redis-cli INFO stats | grep instantaneous_ops
redis-cli KEYS "rl:*" | wc -l  # active rate-limit keys
```

### Check input filter flags

```bash
# Flagged messages (injection attempts, etc.)
journalctl -u saransh-agent --since "24 hours ago" --no-pager | grep 'input_filter.flagged'

# Breakdown by flag type
journalctl -u saransh-agent --since "7 days ago" --no-pager | \
  grep 'input_filter.flagged' | grep -oP '"flags":\[[^\]]*\]' | sort | uniq -c
```

These should be very rare. If you see a spike, someone may be probing the bot.

### Check output filter blocks

```bash
# EVERY entry here is worth investigating — these are attempted leaks
journalctl -u saransh-agent --since "7 days ago" --no-pager | grep 'output_filter.leak_blocked'

# By leak type
journalctl -u saransh-agent --since "7 days ago" --no-pager | \
  grep 'output_filter.leak_blocked' | grep -oP '"leak_type":"[^"]*"' | sort | uniq -c
```

If `leak_type=phone_number` appears often, the LLM may be mentioning other customers — check your system prompt.

### Check lead score distribution

```sql
-- Connect: cd /opt/saransh-saleagent && source .env && psql "$DATABASE_URL"

-- Score distribution across all leads
SELECT
  CASE
    WHEN "leadScore" >= 86 THEN 'READY_FOR_CALL (86-100)'
    WHEN "leadScore" >= 61 THEN 'HOT (61-85)'
    WHEN "leadScore" >= 31 THEN 'WARM (31-60)'
    ELSE 'COLD (0-30)'
  END AS bucket,
  COUNT(*) AS leads,
  ROUND(AVG("leadScore")) AS avg_score
FROM "Lead"
WHERE "leadScore" IS NOT NULL
GROUP BY 1
ORDER BY avg_score DESC;

-- Recent high-score leads (last 7 days)
SELECT name, phone, "leadScore", "createdAt"
FROM "Lead"
WHERE "leadScore" >= 61
  AND "createdAt" > NOW() - INTERVAL '7 days'
ORDER BY "leadScore" DESC;
```

### Check Telegram alert history

```bash
# All Telegram alerts sent by the bot
journalctl -u saransh-agent --since "7 days ago" --no-pager | grep 'Telegram alert sent'

# Failed Telegram alerts (connectivity issues?)
journalctl -u saransh-agent --since "7 days ago" --no-pager | grep 'Telegram alert failed\|Telegram alert error'
```

---

## Content Management

### Add a knowledge document (RAG)

```sql
-- Connect: psql "$DATABASE_URL"

-- 1. Insert the document
INSERT INTO "KnowledgeDocument" (id, title, content, "sourceType", "sourceId", enabled, "createdAt", "updatedAt")
VALUES (
  'c' || substr(md5(random()::text), 1, 24),  -- cuid-like ID
  'Your Document Title',
  'Full document content goes here. Can be multi-paragraph.',
  'FAQ',  -- Options: FAQ, SERVICE, BUSINESS_INFO, PORTFOLIO, POLICY, TESTIMONIAL, CUSTOM
  'manual_unique_id',  -- unique identifier to prevent duplicates
  true,
  NOW(), NOW()
);
```

After inserting, you must **embed the document** for RAG to find it:

```bash
cd /opt/saransh-saleagent && source venv/bin/activate
python3 -c "
import asyncio
from dotenv import load_dotenv; load_dotenv()
from modules.db import init_pool
from agent.rag.ingestion import ingest_document

async def run():
    await init_pool()
    doc_id = await ingest_document(
        title='Your Document Title',
        content='Full document content goes here.',
        source_type='FAQ',
        source_id='manual_unique_id',
    )
    print(f'Ingested: {doc_id}')

asyncio.run(run())
"
```

**When re-embedding triggers:** Only when you change document content. Updating title or metadata doesn't require re-embedding. To re-embed:

```bash
python3 -c "
import asyncio
from dotenv import load_dotenv; load_dotenv()
from modules.db import init_pool
from agent.rag.ingestion import re_embed_document
asyncio.run(init_pool())
asyncio.run(re_embed_document('THE_DOC_ID'))
"
```

### Add a keyword rule

```sql
INSERT INTO "KeywordRule" (id, keywords, "matchType", response, priority, enabled, "createdAt", "updatedAt")
VALUES (
  'c' || substr(md5(random()::text), 1, 24),
  ARRAY['keyword1', 'keyword2'],  -- triggers on any of these
  'CONTAINS',  -- EXACT = full message match, CONTAINS = substring, REGEX = pattern
  'Your canned response here',
  50,  -- priority: 1-100, higher = checked first. Default rules are 10-30.
  true,
  NOW(), NOW()
);
```

**Priority guidance:**
- 1-20: Low priority (fallback rules)
- 21-50: Standard rules
- 51-80: High priority (override others)
- 81-100: Critical (opt-out, emergency)

### Seed a conversation flow

```sql
INSERT INTO "ConversationFlow" (id, name, "triggerType", "triggerValue", "stepsJson", enabled, "createdAt", "updatedAt")
VALUES (
  'c' || substr(md5(random()::text), 1, 24),
  'Logo Enquiry Flow',
  'KEYWORD',  -- KEYWORD, BUTTON, INTENT, MANUAL
  'logo',
  '[
    {"step": 1, "message": "Great choice! What industry is your business in?", "expectInput": true},
    {"step": 2, "message": "Do you have any color preferences?", "expectInput": true},
    {"step": 3, "message": "Perfect. Let me share our logo packages...", "expectInput": false}
  ]'::jsonb,
  true,
  NOW(), NOW()
);
```

### Add a canned response

```sql
INSERT INTO "CannedResponse" (id, shortcode, body, category, "createdBy", "createdAt")
VALUES (
  'c' || substr(md5(random()::text), 1, 24),
  '/pricing',  -- dashboard agents type this to quickly send
  'Our packages start at ₹3,000 for logos, ₹5,000 for packaging, and ₹8,000 for websites.',
  'sales',
  'saransh',
  NOW()
);
```

---

## Scaling Knobs

### Rate limit thresholds

**File:** `agent/security/rate_limit.py` (lines 29-34)

```python
INBOUND_MAX = 20    # per-phone messages per window
INBOUND_WINDOW = 60 # seconds
OUTBOUND_MAX = 15   # per-phone bot replies per window
OUTBOUND_WINDOW = 60
IP_MAX = 100        # per-IP webhook requests per window
IP_WINDOW = 60
```

Edit the constants, restart the service. No DB change needed.

### Lead scoring weights

**File:** `agent/tools.py`, function `compute_lead_score()` (line 173)

| Signal | Points | Notes |
|--------|--------|-------|
| name | +10 | Any name captured |
| businessType | +10 | Industry identified |
| specificNeed | +15 | Clear service need |
| budgetSignal | +20 | Mentioned budget/price |
| timeline | +15 | Has a deadline |
| isDecisionMaker | +15 | Confirmed decision maker |
| waPhone | +10 | Always true (WhatsApp) |
| notes/proactive | +5 | Extra engagement |

Buckets: READY_FOR_CALL (86+), HOT (61-85), WARM (31-60), COLD (0-30)

### RAG retrieval tuning

**File:** `agent/rag/retrieval.py`

| Knob | Line | Default | Effect |
|------|------|---------|--------|
| Vector search candidates | 249 | `limit=20` | More = better recall, slower |
| BM25 search candidates | 250 | `limit=20` | More = better keyword match |
| RRF k parameter | 258 | `k=60` | Higher = less weight to top ranks |
| Final top-N results | 258 | `top_n=5` | More = more context for LLM (costs tokens) |
| Skip-RAG threshold | `should_skip_rag()` | `<8 chars` | Shorter = more skips |

### Enable/disable keyword rules

```sql
-- Disable a rule (keeps it for re-enabling later)
UPDATE "KeywordRule" SET enabled = false WHERE id = 'THE_RULE_ID';

-- Re-enable
UPDATE "KeywordRule" SET enabled = true WHERE id = 'THE_RULE_ID';

-- List all rules with status
SELECT id, keywords[1] AS first_keyword, priority, enabled FROM "KeywordRule" ORDER BY priority DESC;
```

### Input filter max message length

**File:** `agent/security/input_filter.py` (line 14)

```python
MAX_MESSAGE_LENGTH = 4000  # chars
```

---

## Troubleshooting

### Bot not responding

Systematic diagnosis (follow in order):

```bash
# 1. Is the service running?
systemctl status saransh-agent
# If dead: journalctl -u saransh-agent -n 50 --no-pager  (check crash reason)
# Fix: systemctl restart saransh-agent

# 2. Is the port open?
curl -s http://localhost:8000/webhook?hub.mode=subscribe\&hub.verify_token=saranshdesigns_webhook_2024\&hub.challenge=test
# Should return: test

# 3. Is nginx proxying?
curl -s https://agent.saransh.space/webhook?hub.mode=subscribe\&hub.verify_token=saranshdesigns_webhook_2024\&hub.challenge=test
# Should return: test. If not, check: nginx -t && systemctl status nginx

# 4. Is Meta sending webhooks?
journalctl -u saransh-agent --since "5 minutes ago" | grep 'webhook.received'
# If nothing: check Meta App Dashboard → Webhooks → verify URL is https://agent.saransh.space/webhook

# 5. Is HMAC blocking? (signature mismatch)
journalctl -u saransh-agent --since "5 minutes ago" | grep 'webhook.signature'
# If hmac_matches=false: META_APP_SECRET in .env doesn't match Meta dashboard

# 6. Is rate limiting blocking?
journalctl -u saransh-agent --since "5 minutes ago" | grep 'rate_limit.hit'
# If so: check if legitimate or abuse. Can temporarily raise INBOUND_MAX.

# 7. Is Redis down? (non-fatal but check)
redis-cli ping  # Should return PONG
# If not: systemctl restart redis-server

# 8. Is DB down? (fatal for lead capture + RAG)
cd /opt/saransh-saleagent && source .env && psql "$DATABASE_URL" -c "SELECT 1"
# If error: systemctl restart postgresql

# 9. Is OpenAI responding?
journalctl -u saransh-agent --since "5 minutes ago" | grep 'openai\|RateLimitError\|APIError'
# If rate limited: wait or check billing at platform.openai.com

# 10. Check WhatsApp API token
journalctl -u saransh-agent --since "5 minutes ago" | grep 'whatsapp_send\|api_ok'
# If api_ok=false: META_WHATSAPP_TOKEN may have expired (regenerate in Meta dashboard)
```

### Telegram alerts stopped

```bash
# 1. Check if alerts are being triggered at all
journalctl -u saransh-agent --since "24 hours ago" | grep 'Telegram alert'

# 2. Check if token/chat_id are set
grep TELEGRAM /opt/saransh-saleagent/.env

# 3. Test manually
curl -s "https://api.telegram.org/bot$(grep TELEGRAM_BOT_TOKEN /opt/saransh-saleagent/.env | cut -d= -f2)/sendMessage" \
  -d "chat_id=$(grep TELEGRAM_CHAT_ID /opt/saransh-saleagent/.env | cut -d= -f2)" \
  -d "text=Test alert from droplet"
# Should return {"ok":true,...}
```

### Lead scores seem wrong

```bash
# 1. Check what the scoring function receives for a specific phone
journalctl -u saransh-agent --since "7 days ago" | grep 'tools.capture_lead' | tail -5

# 2. Audit a specific lead's collected details
cd /opt/saransh-saleagent && source .env
psql "$DATABASE_URL" -c "
  SELECT \"leadScore\", name, phone, \"createdAt\"
  FROM \"Lead\"
  WHERE phone LIKE '%LAST4DIGITS%'
  ORDER BY \"createdAt\" DESC LIMIT 5;
"

# 3. Check the JSON conversation file (has raw collected details)
cat data/conversations/91XXXXXXXXXX.json | python3 -m json.tool | grep -A20 'collectedDetails'
# Note: if encrypted, you'll see enc:v1:... — that's correct

# 4. Recalculate manually
cd /opt/saransh-saleagent && source venv/bin/activate
python3 -c "
from agent.tools import compute_lead_score, score_bucket
score = compute_lead_score({
    'name': 'Test',
    'businessType': 'salon',
    'specificNeed': 'logo design',
    'budgetSignal': True,
    'timeline': '2 weeks',
    'isDecisionMaker': True,
})
print(f'Score: {score}, Bucket: {score_bucket(score)}')
"
```

### RAG returning irrelevant results

```bash
# 1. Check what RAG retrieves for a specific query
cd /opt/saransh-saleagent && source venv/bin/activate
python3 -c "
import asyncio
from dotenv import load_dotenv; load_dotenv()
from modules.db import init_pool
from agent.rag.retrieval import rag_search

async def test():
    await init_pool()
    result = await rag_search('YOUR QUERY HERE')
    print(f'Hits: {result.retrieval_hits}, Skipped: {result.skipped}')
    for chunk in result.chunks:
        print(f'  [{chunk.source_type}] {chunk.doc_title} (score: {chunk.score:.4f})')
        print(f'    {chunk.content[:100]}...')

asyncio.run(test())
"

# 2. Check document count
psql "$DATABASE_URL" -c 'SELECT "sourceType", COUNT(*) FROM "KnowledgeDocument" WHERE enabled = true GROUP BY 1;'

# 3. Check chunk count and avg token size
psql "$DATABASE_URL" -c 'SELECT COUNT(*), ROUND(AVG("tokenCount")) AS avg_tokens FROM "KnowledgeChunk";'

# 4. If a specific document returns poorly: re-embed it
# (See "Add a knowledge document" section above for re_embed_document command)

# 5. If BM25 returns 0 for Hindi queries: EXPECTED
# tsvector uses English dictionary. Vector search handles Hindi via embeddings.
# This is by design — hybrid search compensates.
```

---

## Secret Rotation Procedure

### META_WHATSAPP_TOKEN (expires periodically)

```bash
# 1. Generate new token in Meta Business Dashboard → WhatsApp → API Setup
# 2. Update .env
sed -i 's/^META_WHATSAPP_TOKEN=.*/META_WHATSAPP_TOKEN=NEW_TOKEN_HERE/' /opt/saransh-saleagent/.env
# 3. Restart (< 2 second downtime)
systemctl restart saransh-agent
# 4. Verify: send a test message from your phone
```

### META_APP_SECRET

```bash
# 1. Get new secret from Meta App Dashboard → Settings → Basic → App Secret
# 2. Update .env
sed -i 's/^META_APP_SECRET=.*/META_APP_SECRET=NEW_SECRET/' /opt/saransh-saleagent/.env
# 3. Restart
systemctl restart saransh-agent
# 4. Verify: send a WhatsApp message, check logs for hmac_matches=true
```

### OPENAI_API_KEY

```bash
# 1. Generate new key at platform.openai.com/api-keys
# 2. Update .env
sed -i 's/^OPENAI_API_KEY=.*/OPENAI_API_KEY=sk-NEW_KEY/' /opt/saransh-saleagent/.env
# 3. Restart
systemctl restart saransh-agent
# 4. Verify: send a message that triggers LLM (not just keyword)
```

### TELEGRAM_BOT_TOKEN

```bash
# 1. Talk to @BotFather on Telegram → /revoke → select bot → get new token
# 2. Update .env
sed -i 's/^TELEGRAM_BOT_TOKEN=.*/TELEGRAM_BOT_TOKEN=NEW_TOKEN/' /opt/saransh-saleagent/.env
# 3. Restart
systemctl restart saransh-agent
# 4. Verify: trigger an escalation and check Telegram
```

### APP_ENCRYPTION_KEY (AES-256-GCM)

**WARNING:** Rotating this key means existing encrypted data becomes unreadable. Follow carefully:

```bash
# 1. Decrypt all existing conversations first
cd /opt/saransh-saleagent && source venv/bin/activate
python3 -c "
import os, json, glob
from dotenv import load_dotenv; load_dotenv()
from modules.secrets_manager import decrypt_conversation_data

for path in glob.glob('data/conversations/*.json'):
    if 'archive' in path: continue
    with open(path) as f:
        data = json.load(f)
    decrypted = decrypt_conversation_data(data)
    with open(path, 'w') as f:
        json.dump(decrypted, f, indent=2, ensure_ascii=False)
    print(f'Decrypted: {os.path.basename(path)}')
"

# 2. Generate new key
NEW_KEY=$(openssl rand -base64 32)
sed -i "s/^APP_ENCRYPTION_KEY=.*/APP_ENCRYPTION_KEY=$NEW_KEY/" .env
echo "New key: $NEW_KEY"

# 3. Restart — new writes will use the new key
systemctl restart saransh-agent

# 4. Re-encrypt existing data with new key
python3 -c "
import os, json, glob
from dotenv import load_dotenv; load_dotenv()
from modules.secrets_manager import encrypt_conversation_data

for path in glob.glob('data/conversations/*.json'):
    if 'archive' in path: continue
    with open(path) as f:
        data = json.load(f)
    encrypted = encrypt_conversation_data(data)
    with open(path, 'w') as f:
        json.dump(encrypted, f, indent=2, ensure_ascii=False)
    print(f'Re-encrypted: {os.path.basename(path)}')
"
```

### DATABASE_URL (Postgres password)

```bash
# 1. Change password in Postgres
sudo -u postgres psql -c "ALTER USER your_user PASSWORD 'new_password';"
# 2. Update .env with new connection string
# 3. Restart
systemctl restart saransh-agent
# 4. Verify: check logs for db.pool_ready
```

### REDIS_URL

```bash
# Usually only changes if you move Redis to a different host
sed -i 's|^REDIS_URL=.*|REDIS_URL=redis://newhost:6379/0|' /opt/saransh-saleagent/.env
systemctl restart saransh-agent
# Verify: check logs for rate_limit.redis_connected
```

---

*Last updated: 2026-04-16 after Phase 5 (security hardening) deployment.*
