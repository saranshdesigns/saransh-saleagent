"""
AI Agent Core — OpenAI powered brain
Handles all message processing, intent detection, smart responses.
Token-efficient: uses gpt-4o-mini for most tasks, gpt-4o only when vision needed.
"""

import json
import time
import os
import base64
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from openai import OpenAI
from dotenv import load_dotenv

from modules.logging_config import get_logger

log = get_logger("saransh.agent.core")

IST = ZoneInfo("Asia/Kolkata")

from agent.tools import TOOLS, execute_tool, compute_lead_score, score_bucket
from agent.rag.retrieval import rag_search, should_skip_rag

from agent.conversation import (
    load_conversation, save_conversation, add_message,
    update_stage, update_service, update_details,
    update_seriousness, add_image, mark_handoff,
    get_recent_messages, get_summary, ConversationStage, ServiceType
)

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

PRICING_PATH = Path("config/pricing.json")
SETTINGS_PATH = Path("config/settings.json")


def load_pricing() -> dict:
    with open(PRICING_PATH, "r") as f:
        return json.load(f)


def load_settings() -> dict:
    with open(SETTINGS_PATH, "r") as f:
        return json.load(f)


# Phase 1.4b.1 / v1.3.3 (Chunk 1) - bot config loaded from saransh_dashboard.BotConfig (DB, not hardcoded).
# 5-second in-process cache for the full singleton row; changes from dashboard propagate within ~5s.
# The full row is cached so message templates (welcome/fallback/call-request/handoff) share one DB hit.
_BOT_CONFIG_CACHE = {"value": None, "fetched_at": 0.0}
_BOT_CONFIG_TTL = 5.0

# Cold-boot defaults — preserved verbatim from previous hardcoded literals so behaviour
# is unchanged when DB is unreachable or BotConfig columns are NULL.
_FALLBACK_PROMPT = "You are a helpful business assistant. Do not make up information."
DEFAULT_WELCOME_MESSAGE = "Welcome to SaranshDesigns. How can I assist you today?"
DEFAULT_FALLBACK_MESSAGE = "I appreciate your message! Let me connect you with Saransh Sharma sir for this."
DEFAULT_CALL_REQUEST_MESSAGE = "Sure, I will arrange a call for you. Please wait, I'll coordinate with Saransh Sir and you will receive a call shortly."
DEFAULT_HANDOFF_MESSAGE = "Hi! Your enquiry has already been noted and Saransh Sir will be in touch with you shortly. Please wait for his message!"

# Chunk 2: LLM model + sampling + tool budget defaults — preserve current behaviour exactly.
DEFAULT_REPLY_MODEL = "gpt-4o-mini"
DEFAULT_VISION_MODEL = "gpt-4o"
DEFAULT_REPLY_TEMPERATURE = 0.7
DEFAULT_TOOL_CALL_CAP = 3

# Chunk 3: persona / tone / names / language / hot-lead defaults.
# Empty strings for free-text additive fields => no-op when blank, master prompt is unchanged.
DEFAULT_PERSONA_TRAITS = ""
DEFAULT_TONE_EXAMPLES = ""
DEFAULT_BOT_NAME = "SaranshDesigns Assistant"
DEFAULT_BUSINESS_NAME = "SaranshDesigns"
# TODO Chunk 3.5: wire off-hours message when workingHoursJson is wired
DEFAULT_OFF_HOURS_MESSAGE = ""
# BotConfig field is on a 0-10 scale per v1.3 spec; bot scoring is on 0-100.
# Bridge by multiplying the BotConfig value by 10 in the accessor — preserves current
# scoring math, lets dashboard expose a friendlier 0-10 slider.
DEFAULT_HOT_LEAD_THRESHOLD = 7
DEFAULT_LANGUAGE_MODE = "ENGLISH_ONLY"

# Chunk 4: signal weights — drives agreement/rejection score deltas.
# Mirrors the dashboard default at saransh-dashboard/backend/src/routes/bot-config-advanced.js.
# Values are on a small (~1-3) scale per BotConfig contract; the bot multiplies by 10
# at apply time to bridge to its 0-100 seriousness scale (same pattern as hotLeadThreshold).
DEFAULT_SIGNAL_WEIGHTS = {
    "asked_pricing": 2,
    "shared_budget": 3,
    "requested_call": 3,
    "specific_project": 2,
    "shared_timeline": 2,
    "agreement": 2,
    "rejection": -1,
    "off_topic": -1,
}

# Chunk 5: rate limit + daily spend kill-switch defaults — preserve current behaviour
# when DB columns are NULL.
DEFAULT_RATE_LIMIT_PER_HOUR = 60
DEFAULT_DAILY_SPEND_LIMIT_USD = 20.0

# OpenAI April 2026 price table (USD per 1M tokens). Source: OpenAI public pricing
# page snapshot taken 2026-04-29 — gpt-4o-mini $0.150 in / $0.600 out, gpt-4o
# $2.50 in / $10.00 out, text-embedding-3-small $0.020. Hardcoded here so the
# kill-switch keeps working even when the dashboard route is unreachable; LLMUsage
# table integration is deferred to bot-v2 per the plan.
_OPENAI_PRICE_PER_1M = {
    "gpt-4o-mini": {"in": 0.150, "out": 0.600},
    "gpt-4o": {"in": 2.50, "out": 10.00},
    "text-embedding-3-small": {"in": 0.020, "out": 0.020},
}

# In-memory daily spend tracker — IST midnight reset, single-process counter.
# Bot is single-instance today so process-local is sufficient; a future multi-worker
# deployment would migrate this to Redis / Postgres (deferred to bot-v2).
_DAILY_SPEND_USD = 0.0
_DAILY_SPEND_DATE = None  # initialised lazily on first call (date in IST)
_DAILY_SPEND_ALERT_SENT = False  # one-shot owner alert per IST day

# Track which (field, source) pairs have logged once per process (info-level, not per request).
_LOGGED: set = set()


async def _load_bot_config() -> dict:
    """Fetch the BotConfig singleton row with a 5s TTL cache. Returns a dict (possibly empty on failure).

    Defensive: any column may be NULL — callers must apply their own DEFAULT_* fallback.
    """
    now = time.monotonic()
    cached = _BOT_CONFIG_CACHE["value"]
    if cached is not None and (now - _BOT_CONFIG_CACHE["fetched_at"]) < _BOT_CONFIG_TTL:
        return cached
    try:
        from modules.db import _pool, _pool_ok
        if not _pool_ok():
            log.warning("bot_config.pool_unavailable")
            return cached or {}
        async with _pool.acquire() as conn:
            row = await conn.fetchrow(
                'SELECT "masterPrompt", "welcomeMessage", "fallbackMessage", '
                '"callRequestMessage", "handoffMessage", "botName", "businessName", '
                '"llmReplyModel", "llmVisionModel", "llmReplyTemperature", "toolCallCap", '
                '"personaTraits", "toneExamples", "offHoursMessage", '
                '"hotLeadThreshold", "languageMode", '
                '"signalWeightsJson", "rateLimitPerHour", "dailySpendLimitUsd" '
                'FROM "BotConfig" WHERE id = $1',
                "singleton",
            )
        value = dict(row) if row else {}
        _BOT_CONFIG_CACHE["value"] = value
        _BOT_CONFIG_CACHE["fetched_at"] = now
        log.info("bot_config.loaded", keys=sorted(value.keys()))
        return value
    except Exception as e:
        log.warning("bot_config.load_failed", error=str(e))
        return cached or {}


async def _load_master_prompt() -> str:
    """Backwards-compatible wrapper — returns just the masterPrompt string for existing call sites."""
    cfg = await _load_bot_config()
    value = (cfg.get("masterPrompt") if cfg else None) or _FALLBACK_PROMPT
    return value


def _resolve_field(cfg: dict, field: str, default: str) -> str:
    """Pick DB value if non-null/non-empty, else fall back to the existing literal default.
    Logs the source once per (process, field, source)."""
    raw = cfg.get(field) if cfg else None
    if isinstance(raw, str) and raw.strip():
        source = "db"
        value = raw
    else:
        source = "default_fallback"
        value = default
    key = (field, source)
    if key not in _LOGGED:
        _LOGGED.add(key)
        log.info("bot_config.loaded", source=source, field=field)
    return value


def _resolve_typed_field(cfg: dict, field: str, default, log_field: str):
    """Like _resolve_field but for non-string values (numeric, model name strings).
    Accepts DB value if not None; numeric defaults preserve int/float types.
    Logs the source once per (process, field, source)."""
    raw = cfg.get(field) if cfg else None
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        source = "default_fallback"
        value = default
    else:
        source = "db"
        value = raw
    key = (field, source)
    if key not in _LOGGED:
        _LOGGED.add(key)
        log.info("bot_config.loaded", source=source, field=log_field)
    return value


async def _get_welcome_message() -> str:
    cfg = await _load_bot_config()
    return _resolve_field(cfg, "welcomeMessage", DEFAULT_WELCOME_MESSAGE)


async def _get_fallback_message() -> str:
    cfg = await _load_bot_config()
    return _resolve_field(cfg, "fallbackMessage", DEFAULT_FALLBACK_MESSAGE)


async def _get_call_request_message() -> str:
    cfg = await _load_bot_config()
    return _resolve_field(cfg, "callRequestMessage", DEFAULT_CALL_REQUEST_MESSAGE)


async def _get_handoff_message() -> str:
    cfg = await _load_bot_config()
    return _resolve_field(cfg, "handoffMessage", DEFAULT_HANDOFF_MESSAGE)


# Chunk 2 accessors — LLM model + sampling + tool budget. NULL columns => DEFAULT_* fallbacks
# preserving today's hardcoded behaviour.

async def _get_reply_model() -> str:
    cfg = await _load_bot_config()
    return _resolve_typed_field(cfg, "llmReplyModel", DEFAULT_REPLY_MODEL, "llm_reply_model")


async def _get_vision_model() -> str:
    cfg = await _load_bot_config()
    return _resolve_typed_field(cfg, "llmVisionModel", DEFAULT_VISION_MODEL, "llm_vision_model")


async def _get_reply_temperature() -> float:
    cfg = await _load_bot_config()
    return _resolve_typed_field(cfg, "llmReplyTemperature", DEFAULT_REPLY_TEMPERATURE, "llm_reply_temperature")


async def _get_tool_call_cap() -> int:
    cfg = await _load_bot_config()
    return _resolve_typed_field(cfg, "toolCallCap", DEFAULT_TOOL_CALL_CAP, "tool_call_cap")


# Chunk 3 accessors — persona / tone / names / language / hot-lead.
# Each returns the configured DB value if non-null/non-empty, otherwise the DEFAULT_*
# preserves today's hardcoded behaviour. Free-text fields default to empty strings —
# callers treat empty as "no extra context to inject" and the master prompt is unchanged.

async def _get_persona_traits() -> str:
    cfg = await _load_bot_config()
    return _resolve_field(cfg, "personaTraits", DEFAULT_PERSONA_TRAITS)


async def _get_tone_examples() -> str:
    cfg = await _load_bot_config()
    return _resolve_field(cfg, "toneExamples", DEFAULT_TONE_EXAMPLES)


async def _get_bot_name() -> str:
    cfg = await _load_bot_config()
    return _resolve_field(cfg, "botName", DEFAULT_BOT_NAME)


async def _get_business_name() -> str:
    cfg = await _load_bot_config()
    return _resolve_field(cfg, "businessName", DEFAULT_BUSINESS_NAME)


# TODO Chunk 3.5: wire off-hours message when workingHoursJson is wired.
# Accessor exposed now so a later chunk can use it without touching this module again.
async def _get_off_hours_message() -> str:
    cfg = await _load_bot_config()
    return _resolve_field(cfg, "offHoursMessage", DEFAULT_OFF_HOURS_MESSAGE)


async def _get_hot_lead_threshold() -> int:
    """Hot-lead trigger threshold on the bot's 0-100 scale.

    BotConfig.hotLeadThreshold is on a 0-10 scale (v1.3 spec). We multiply by 10
    here so existing seriousness arithmetic (which lives on 0-100) stays untouched.
    Default 7 → 70/100, matching the audit's example threshold.
    """
    cfg = await _load_bot_config()
    raw = _resolve_typed_field(cfg, "hotLeadThreshold", DEFAULT_HOT_LEAD_THRESHOLD, "hot_lead_threshold")
    try:
        return int(raw) * 10
    except (TypeError, ValueError):
        return DEFAULT_HOT_LEAD_THRESHOLD * 10


async def _get_language_mode() -> str:
    cfg = await _load_bot_config()
    value = _resolve_typed_field(cfg, "languageMode", DEFAULT_LANGUAGE_MODE, "language_mode")
    # Defensive: enum value must be one of the three known modes; anything else
    # falls back to the default so a typo in the DB cannot silently break replies.
    if value not in ("ENGLISH_ONLY", "AUTO_MIRROR", "CUSTOMER_LANGUAGE_ONLY"):
        return DEFAULT_LANGUAGE_MODE
    return value


# Chunk 4 accessor — signal weights drive scoring deltas.
async def _get_signal_weights() -> dict:
    """Return the merged signal-weights dict.

    Falls through to DEFAULT_SIGNAL_WEIGHTS if the column is NULL, malformed,
    or contains non-numeric values. Partial overrides are merged on top of the
    defaults so missing keys keep their default (e.g. dashboard sets only
    "agreement"=3 and the rest stay default).
    """
    cfg = await _load_bot_config()
    raw = cfg.get("signalWeightsJson") if cfg else None
    # asyncpg returns Json columns as already-parsed dicts/lists; tolerate str too
    # in case some other client wrote a raw JSON string.
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = None
    if not isinstance(raw, dict):
        key = ("signalWeightsJson", "default_fallback")
        if key not in _LOGGED:
            _LOGGED.add(key)
            log.info("bot_config.loaded", source="default_fallback", field="signal_weights")
        return dict(DEFAULT_SIGNAL_WEIGHTS)
    merged = dict(DEFAULT_SIGNAL_WEIGHTS)
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, (int, float)):
            merged[k] = v
    key = ("signalWeightsJson", "db")
    if key not in _LOGGED:
        _LOGGED.add(key)
        log.info("bot_config.loaded", source="db", field="signal_weights")
    return merged


# Chunk 5 accessors — rate limit + daily spend kill-switch.
async def _get_rate_limit_per_hour() -> int:
    """Per-phone inbound message cap per rolling hour."""
    cfg = await _load_bot_config()
    raw = _resolve_typed_field(cfg, "rateLimitPerHour", DEFAULT_RATE_LIMIT_PER_HOUR, "rate_limit_per_hour")
    try:
        v = int(raw)
        return v if v > 0 else DEFAULT_RATE_LIMIT_PER_HOUR
    except (TypeError, ValueError):
        return DEFAULT_RATE_LIMIT_PER_HOUR


async def _get_daily_spend_limit_usd() -> float:
    """Hard daily spend cap (USD). Crossing it engages the kill-switch."""
    cfg = await _load_bot_config()
    raw = _resolve_typed_field(cfg, "dailySpendLimitUsd", DEFAULT_DAILY_SPEND_LIMIT_USD, "daily_spend_limit_usd")
    try:
        v = float(raw)
        return v if v > 0 else DEFAULT_DAILY_SPEND_LIMIT_USD
    except (TypeError, ValueError):
        return DEFAULT_DAILY_SPEND_LIMIT_USD


def _today_ist_date():
    """IST calendar date — used as the bucket key for daily spend reset."""
    return datetime.now(IST).date()


def _maybe_reset_daily_spend():
    """Reset the in-memory spend counter when the IST date rolls over."""
    global _DAILY_SPEND_USD, _DAILY_SPEND_DATE, _DAILY_SPEND_ALERT_SENT
    today = _today_ist_date()
    if _DAILY_SPEND_DATE != today:
        _DAILY_SPEND_USD = 0.0
        _DAILY_SPEND_DATE = today
        _DAILY_SPEND_ALERT_SENT = False


def _record_openai_cost(model: str, response) -> None:
    """Add the cost of one OpenAI completion to today's spend counter.

    Uses response.usage.prompt_tokens / completion_tokens against the hardcoded
    April 2026 price table. Unknown models are scored as gpt-4o-mini (cheapest)
    so a typo in BotConfig can never silently inflate the kill-switch trigger.
    Defensive: any exception is swallowed — accounting must never break replies.
    """
    global _DAILY_SPEND_USD
    try:
        _maybe_reset_daily_spend()
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        # Strip provider prefix (e.g. "openai/gpt-4o-mini") and use the leaf name.
        leaf = (model or "").split("/")[-1].strip()
        price = _OPENAI_PRICE_PER_1M.get(leaf) or _OPENAI_PRICE_PER_1M["gpt-4o-mini"]
        cost = (prompt_tokens / 1_000_000) * price["in"] + (completion_tokens / 1_000_000) * price["out"]
        _DAILY_SPEND_USD += cost
        log.debug(
            "daily_spend.record",
            model=leaf,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=round(cost, 6),
            today_total_usd=round(_DAILY_SPEND_USD, 4),
        )
    except Exception as e:
        log.warning("daily_spend.record_failed", error=str(e))


async def _is_daily_spend_exhausted() -> bool:
    """Check the kill-switch. Returns True if today's spend has hit the configured limit.

    Side effect: fires a one-shot owner alert per IST day on first crossing.
    Logs once per crossing as 'daily_spend.kill_switch_engaged amount=X.XX limit=Y.YY'.
    """
    global _DAILY_SPEND_ALERT_SENT
    _maybe_reset_daily_spend()
    try:
        limit = await _get_daily_spend_limit_usd()
    except Exception:
        limit = DEFAULT_DAILY_SPEND_LIMIT_USD
    if _DAILY_SPEND_USD < limit:
        return False
    log.warning(
        "daily_spend.kill_switch_engaged",
        amount=round(_DAILY_SPEND_USD, 4),
        limit=round(limit, 4),
    )
    if not _DAILY_SPEND_ALERT_SENT:
        _DAILY_SPEND_ALERT_SENT = True
        try:
            from agent.whatsapp import send_owner_alert
            await send_owner_alert({
                "phone": "system",
                "summary": f"Daily LLM spend kill-switch engaged: ${_DAILY_SPEND_USD:.2f} >= ${limit:.2f}. Bot is returning fallback replies until midnight IST.",
            })
        except Exception as e:
            log.warning("daily_spend.alert_send_failed", error=str(e))
    return True


def _get_reply_model_sync() -> str:
    """Sync read for non-async call sites — uses cached BotConfig if warm, else default.
    Behaviour-preserving: falls back to DEFAULT_REPLY_MODEL on cold cache, matching pre-Chunk-2 hardcode."""
    cfg = _BOT_CONFIG_CACHE["value"] or {}
    return _resolve_typed_field(cfg, "llmReplyModel", DEFAULT_REPLY_MODEL, "llm_reply_model")


async def build_messages_for_openai(phone: str, new_message: str, image_data: str = None) -> list:
    """Build the message list to send to OpenAI, including conversation history."""
    settings = load_settings()
    pricing = load_pricing()

    # Inject current pricing into system prompt
    pricing_context = f"""
## CURRENT LIVE PRICING
Logo Package: ₹{pricing['logo']['logo_package']['price']} (min ₹{pricing['logo']['logo_package']['min_price']})
Branding Package: ₹{pricing['logo']['branding_package']['price']}
Packaging Pouch Master: ₹{pricing['packaging']['pouch']['master']['price']} (min ₹{pricing['packaging']['pouch']['master']['min_price']})
Packaging Pouch Variant: ₹{pricing['packaging']['pouch']['variant']['price']} (min ₹{pricing['packaging']['pouch']['variant']['min_price']})
Packaging Label Master: ₹{pricing['packaging']['label']['master']['price']} (min ₹{pricing['packaging']['label']['master']['min_price']})
Packaging Box Master: ₹{pricing['packaging']['box']['master']['price']} (min ₹{pricing['packaging']['box']['master']['min_price']})
Website Starter: ₹{pricing['website']['starter']['price_min']}–₹{pricing['website']['starter']['price_max']} (advance: ₹{pricing['website']['starter']['advance']})
Website Business: ₹{pricing['website']['business']['price_min']}–₹{pricing['website']['business']['price_max']} (advance: ₹{pricing['website']['business']['advance']})
Website Premium: ₹{pricing['website']['premium']['price_min']}–₹{pricing['website']['premium']['price_max']} (advance: ₹{pricing['website']['premium']['advance']})
Website Ecommerce (Shopify): ₹{pricing['website']['ecommerce']['price_min']}–₹{pricing['website']['ecommerce']['price_max']} (advance: ₹{pricing['website']['ecommerce']['advance']})
"""

    conv = load_conversation(phone)

    # Current time for greeting — always IST (Asia/Kolkata)
    now = datetime.now(IST)
    hour = now.hour
    if 5 <= hour < 12:
        time_greeting = "Good morning"
        time_period = "morning"
    elif 12 <= hour < 17:
        time_greeting = "Good afternoon"
        time_period = "afternoon"
    else:
        time_greeting = "Good evening"
        time_period = "evening"

    # Projects summary for multi-project context
    projects = conv.get("projects", [])
    projects_context = ""
    if projects:
        projects_context = "\nProjects:\n"
        for i, p in enumerate(projects):
            projects_context += f"  Project {p['id']} ({p['service']}): {json.dumps(p['details'], ensure_ascii=False)} — stage: {p['stage']}\n"

    # Existing logo images
    existing_logos = [img for img in conv.get("images_received", []) if img.get("tag") == "existing_logo"]
    existing_logo_context = f"\nExisting Logo Images Received: {len(existing_logos)} (redesign — not a fresh logo)" if existing_logos else ""

    # Phase 1.4b.1: dashboard writes custom_instructions as a STRING; legacy runtime may have it as a DICT keyed by service. Tolerate both.
    custom_instructions = settings.get("custom_instructions", {})
    service_key = conv.get("service", "unknown")
    custom_ctx = ""
    if isinstance(custom_instructions, dict):
        ci_for_service = custom_instructions.get(service_key)
        if isinstance(ci_for_service, str) and ci_for_service.strip():
            custom_ctx += f"\n## OWNER CUSTOM INSTRUCTIONS FOR {service_key.upper()} SERVICE\n(Follow these — set by the business owner, take priority over defaults)\n{ci_for_service}\n"
        general_ci = custom_instructions.get("general", "")
        if isinstance(general_ci, str) and general_ci.strip():
            custom_ctx += f"\n## GENERAL OWNER INSTRUCTIONS\n{general_ci}\n"
    elif isinstance(custom_instructions, str) and custom_instructions.strip():
        custom_ctx += f"\n## OWNER CUSTOM INSTRUCTIONS\n(Set by the business owner via dashboard, take priority over defaults)\n{custom_instructions}\n"

    # Inject knowledge base FAQ
    knowledge_base = settings.get("knowledge_base", [])
    kb_ctx = ""
    if knowledge_base:
        kb_lines = [f"Q: {e['question']}\nA: {e['answer']}" for e in knowledge_base]
        kb_ctx = "\n## KNOWLEDGE BASE — FAQ (Use these answers when clients ask similar questions)\n" + "\n\n".join(kb_lines) + "\n"

    # Chunk 3: load master prompt first, then layer dashboard-controlled persona /
    # tone / names / language directives BEFORE pricing / custom_ctx / KB context.
    # Each block is only appended when it actually has content — when every field is
    # NULL or default, master_prompt is byte-for-byte what it was pre-Chunk-3.
    master_prompt = await _load_master_prompt()
    bot_name = await _get_bot_name()
    business_name = await _get_business_name()
    persona = await _get_persona_traits()
    tone = await _get_tone_examples()
    lang_mode = await _get_language_mode()

    prompt_extras = []
    if bot_name and bot_name != DEFAULT_BOT_NAME:
        prompt_extras.append(f"YOUR NAME: {bot_name}")
    if business_name and business_name != DEFAULT_BUSINESS_NAME:
        prompt_extras.append(f"BUSINESS: {business_name}")
    if persona:
        prompt_extras.append(f"PERSONA TRAITS:\n{persona}")
    if tone:
        prompt_extras.append(f"TONE EXAMPLES:\n{tone}")
    if lang_mode == "ENGLISH_ONLY":
        prompt_extras.append("LANGUAGE: Always reply in English regardless of the customer's language.")
    elif lang_mode == "AUTO_MIRROR":
        prompt_extras.append("LANGUAGE: Detect the customer's language and reply in the same language. Default to English when unclear.")
    elif lang_mode == "CUSTOMER_LANGUAGE_ONLY":
        prompt_extras.append("LANGUAGE: Reply in the customer's language only. Never switch to English unless they do.")
    if prompt_extras:
        master_prompt = master_prompt + "\n\n" + "\n\n".join(prompt_extras)

    system_with_context = master_prompt + pricing_context + custom_ctx + kb_ctx + f"""
## CURRENT TIME (IST — India Standard Time)
Time: {now.strftime('%I:%M %p')} IST | Period: {time_period}
→ If "Is First Message" is True below, your reply MUST start with "{time_greeting}!"

## CURRENT CONVERSATION STATE
Stage: {conv['stage']}
Service: {conv['service']}
Collected Details: {json.dumps(conv['collected_details'], ensure_ascii=False)}
Seriousness Score: {conv['seriousness_score']}/100
Images Received: {len(conv['images_received'])}{existing_logo_context}
Notes: {conv['notes']}
Is First Message: {len(conv['messages']) <= 1}{projects_context}
"""

    messages = [{"role": "system", "content": system_with_context}]

    # Add conversation history (last 15 messages)
    history = get_recent_messages(phone, count=15)
    for msg in history:
        role = msg["role"]
        # Translate 'owner' role to 'assistant' — OpenAI only accepts user/assistant/system.
        # Owner messages are treated as if the AI said them, so it continues naturally.
        if role == "owner":
            role = "assistant"

        if role == "user" and msg.get("image_url"):
            # Previous image messages — include as text reference
            messages.append({
                "role": "user",
                "content": f"[Client sent an image: {msg.get('content', 'reference image')}]"
            })
        else:
            messages.append({
                "role": role,
                "content": msg["content"]
            })

    # Add new message
    if image_data:
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": new_message or "Please analyze this image I've sent."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}}
            ]
        })
    else:
        messages.append({"role": "user", "content": new_message})

    return messages


def detect_intent(message: str) -> dict:
    """Quick intent detection without full conversation context. Token-efficient."""
    intent_model = _get_reply_model_sync()
    response = client.chat.completions.create(
        model=intent_model,
        messages=[
            {
                "role": "system",
                "content": """Detect intent from this WhatsApp message for a branding/design business.
Return JSON only with:
{
  "service": "logo" | "packaging" | "website" | "unknown",
  "intent": "new_lead" | "question" | "portfolio_request" | "call_request" | "price_check" | "sample_request" | "negotiation" | "agreement" | "other",
  "urgency": "high" | "medium" | "low"
}"""
            },
            {"role": "user", "content": message}
        ],
        max_tokens=100,
        response_format={"type": "json_object"}
    )
    # Chunk 5: account this small probe call against the daily-spend budget too.
    _record_openai_cost(intent_model, response)
    try:
        return json.loads(response.choices[0].message.content)
    except Exception:
        return {"service": "unknown", "intent": "other", "urgency": "low"}


def _get_ist_greeting() -> str:
    """Return time-appropriate greeting based on current IST time."""
    hour = datetime.now(IST).hour
    if 5 <= hour < 12:
        return "Good morning!"
    elif 12 <= hour < 17:
        return "Good afternoon!"
    else:
        return "Good evening!"


async def process_message(phone: str, message: str, image_data: str = None, wamid: str = None) -> str:
    """
    Main entry point. Process incoming message and return agent's reply.
    Uses gpt-4o-mini normally, gpt-4o if image is present.
    Phase 3: includes structured tool calling with strict=true.
    """
    from modules.logging_config import get_logger
    _log = get_logger("saransh.agent.core")

    conv = load_conversation(phone)
    is_first_message = len(conv.get("messages", [])) == 0

    # Save incoming message
    add_message(phone, "user", message, image_url="[image]" if image_data else None, wamid=wamid)

    # Quick intent detection for routing (cheap call)
    intent = detect_intent(message)

    # Update service if detected and unknown so far
    if conv["service"] == ServiceType.UNKNOWN and intent["service"] != "unknown":
        update_service(phone, intent["service"])

    # Seriousness: any user reply earns a small engagement bonus (+3).
    # Chunk 4 NOTE: this bonus is intentionally NOT routed through signalWeightsJson
    # yet — there is no operator-facing toggle for "engagement bonus per reply" in
    # the dashboard contract today, and changing the magnitude would shift the
    # baseline scoring curve for every conversation. Preserved as-is to avoid
    # silently changing scoring semantics; revisit when an explicit
    # "engagement_bonus" key is added to BotConfig.signalWeightsJson.
    update_seriousness(phone, 3)

    # Build full message context
    messages = await build_messages_for_openai(phone, message, image_data)

    # Phase 4: RAG context injection — enrich system prompt with relevant KB chunks
    rag_context = ""
    rag_stats = {"embedding_tokens": 0, "retrieval_hits": 0}
    if not image_data and message and not should_skip_rag(message):
        try:
            rag_result = await rag_search(message)
            if rag_result.context:
                rag_context = rag_result.context
                rag_stats["embedding_tokens"] = rag_result.embedding_tokens
                rag_stats["retrieval_hits"] = rag_result.retrieval_hits
                # Inject as additional system context
                rag_block = (
                    "\n\n## KNOWLEDGE BASE CONTEXT (retrieved via RAG — use these to answer)\n"
                    + rag_context
                    + "\n\nUse the above knowledge to answer accurately. Cite specific details when relevant."
                )
                # Append to the system message
                if messages and messages[0]["role"] == "system":
                    messages[0]["content"] += rag_block
                _log.info("core.rag_injected",
                         hits=rag_stats["retrieval_hits"],
                         embedding_tokens=rag_stats["embedding_tokens"])
        except Exception as e:
            _log.warning("core.rag_error", error=str(e))

    # Choose model
    model = await _get_vision_model() if image_data else await _get_reply_model()
    reply_temperature = await _get_reply_temperature()
    tool_call_cap = await _get_tool_call_cap()

    # Chunk 5: daily-spend kill-switch. If today's running total has hit
    # BotConfig.dailySpendLimitUsd we return a graceful fallback and skip the
    # OpenAI call entirely. The kill-switch is checked once per inbound (here)
    # and once per follow-up tool round below — both gate on the same in-memory
    # counter, so a single spike during a tool loop trips immediately.
    if await _is_daily_spend_exhausted():
        fallback = "I'm temporarily unavailable. Saransh will reply directly shortly."
        add_message(phone, "assistant", fallback)
        return fallback

    # Phase 3: call with tools (strict=true), parallel_tool_calls=false
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        tools=TOOLS,
        parallel_tool_calls=False,
        max_tokens=600,
        temperature=reply_temperature,
    )
    _record_openai_cost(model, response)

    msg = response.choices[0].message

    # Handle refusal (OpenAI safety)
    if hasattr(msg, "refusal") and msg.refusal:
        _log.warning("core.llm_refusal", refusal=msg.refusal)
        reply = await _get_fallback_message()
    # Handle tool calls — execute and feed results back (max 3 rounds)
    elif msg.tool_calls:
        messages.append(msg)
        for _round in range(tool_call_cap):
            for tool_call in msg.tool_calls:
                fn_name = tool_call.function.name
                try:
                    fn_args = json.loads(tool_call.function.arguments)
                except Exception:
                    fn_args = {}
                _log.info("core.tool_call", tool=fn_name, round=_round)
                result = await execute_tool(fn_name, fn_args, phone)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })
            # Chunk 5: re-check kill-switch before each follow-up call so a
            # tool-heavy conversation can't push spend significantly past the cap.
            if await _is_daily_spend_exhausted():
                msg = type("EmptyMsg", (), {"content": "I'm temporarily unavailable. Saransh will reply directly shortly.", "tool_calls": None})()
                break
            # Get next response
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOLS,
                parallel_tool_calls=False,
                max_tokens=600,
                temperature=reply_temperature,
            )
            _record_openai_cost(model, response)
            msg = response.choices[0].message
            if not msg.tool_calls:
                break
            messages.append(msg)
        reply = (msg.content or "").strip()
        if not reply:
            reply = "Let me connect you with Saransh Sharma sir for more details."
    else:
        reply = (msg.content or "").strip()

    # Hardcode greeting on first message — don't rely on AI to do it
    _greeting_words = ("good morning", "good afternoon", "good evening")
    if is_first_message and not reply.lower().startswith(_greeting_words):
        reply = f"{_get_ist_greeting()} {reply}"

    # Save assistant response
    add_message(phone, "assistant", reply)

    # Auto-detect stage changes from reply content
    await _update_stage_from_reply(phone, reply, message)

    # Extract and store structured client details silently
    _extract_and_store_details(phone)

    return reply


async def _update_stage_from_reply(phone: str, reply: str, user_msg: str):
    """Auto-detect and update conversation stage based on reply content.

    Chunk 3: also fires a hot-lead alert to Saransh (idempotent, once per
    conversation) when seriousness crosses BotConfig.hotLeadThreshold.
    """
    reply_lower = reply.lower()
    user_lower = user_msg.lower()
    conv = load_conversation(phone)

    # Handoff triggered
    if "owner will message you shortly" in reply_lower or "connect you with the owner" in reply_lower:
        if not conv["handoff_triggered"]:
            mark_handoff(phone, conv.get("agreed_price"))
        return

    # Escalation
    if "owner alert" in reply_lower:
        update_stage(phone, ConversationStage.ESCALATED)
        return

    # Pricing presented
    if "₹" in reply and conv["stage"] in [ConversationStage.COLLECTING_DETAILS, ConversationStage.CONFIRMING_DETAILS]:
        update_stage(phone, ConversationStage.PRESENTING_PRICING)
        return

    # Seriousness updates from user message.
    # Chunk 4: deltas now sourced from BotConfig.signalWeightsJson. DB values are
    # on the BotConfig 1-3 scale; we multiply by 10 to bridge to the bot's 0-100
    # seriousness scale (same pattern as hotLeadThreshold). NULL/missing column
    # falls through to DEFAULT_SIGNAL_WEIGHTS which mirrors the dashboard default
    # (agreement=2 -> +20, rejection=-1 -> -10). The previous hardcoded values
    # were +10/-5; this is an intentional, operator-controlled change made visible
    # via the dashboard slider.
    signal_weights = await _get_signal_weights()
    agreement_words = ["okay", "ok", "yes", "sure", "agreed", "fine", "deal", "proceed", "haan", "theek", "chalega"]
    if any(word in user_lower for word in agreement_words):
        try:
            agreement_delta = int(round(float(signal_weights.get("agreement", DEFAULT_SIGNAL_WEIGHTS["agreement"])) * 10))
        except (TypeError, ValueError):
            agreement_delta = DEFAULT_SIGNAL_WEIGHTS["agreement"] * 10
        update_seriousness(phone, agreement_delta)

    rejection_words = ["no", "nahi", "nope", "not interested", "too expensive", "bahut zyada"]
    if any(word in user_lower for word in rejection_words):
        try:
            rejection_delta = int(round(float(signal_weights.get("rejection", DEFAULT_SIGNAL_WEIGHTS["rejection"])) * 10))
        except (TypeError, ValueError):
            rejection_delta = DEFAULT_SIGNAL_WEIGHTS["rejection"] * 10
        update_seriousness(phone, rejection_delta)

    # Hot-lead trigger — fire owner alert once when score crosses threshold.
    # Idempotency: a sentinel string is appended to conv['notes'] on first fire so
    # subsequent crossings don't re-page Saransh. (BotConversation.hotLeadAlertSentAt
    # would be the cleaner store but isn't on the model yet — see audit Section E.)
    try:
        post_update_conv = load_conversation(phone)
        score = int(post_update_conv.get("seriousness_score", 0) or 0)
        threshold = await _get_hot_lead_threshold()
        already_alerted = any(
            isinstance(n, str) and n.startswith("hot_lead_alert_sent")
            for n in post_update_conv.get("notes", [])
        )
        if score >= threshold and not already_alerted and not post_update_conv.get("handoff_triggered"):
            from agent.whatsapp import send_owner_alert
            summary = get_summary(phone)
            await send_owner_alert(summary)
            # Mark idempotency sentinel directly on disk — using update_details would
            # collide with structured fields, so we append to notes via a fresh load+save.
            marker_conv = load_conversation(phone)
            marker_conv.setdefault("notes", []).append(
                f"hot_lead_alert_sent score={score} threshold={threshold}"
            )
            save_conversation(phone, marker_conv)
            log.info("core.hot_lead_alert_fired", phone_tail=phone[-4:], score=score, threshold=threshold)
    except Exception as e:
        log.warning("core.hot_lead_alert_failed", error=str(e))


def _extract_and_store_details(phone: str):
    """
    After each AI turn, extract structured client details from conversation history
    and store them in collected_details. Also captures agreed_price if confirmed.
    Uses a cheap gpt-4o-mini call — runs silently in the background.
    """
    conv = load_conversation(phone)
    service = conv.get("service", "unknown")

    recent = get_recent_messages(phone, count=20)
    conv_text = "\n".join(
        f"{m['role'].upper()}: {m['content']}"
        for m in recent
        if m.get("content")
    )
    if not conv_text.strip():
        return

    try:
        extract_model = _get_reply_model_sync()
        response = client.chat.completions.create(
            model=extract_model,
            messages=[
                {
                    "role": "system",
                    "content": f"""Extract client details from this WhatsApp sales conversation for a branding studio.
Service: {service}

Return JSON with only the fields that are CLEARLY confirmed by the client (set unmentioned fields to null):
{{
  "brand_name": "string or null — the client's brand name",
  "logo_style": "string or null — preferred logo style (wordmark/icon+text/emblem/minimal)",
  "tagline": "string or null — brand tagline",
  "products": "string or null — what products need packaging (comma separated)",
  "variant_count": "number or null — how many variants/products",
  "packaging_type": "pouch|box|label|sachet|jar or null",
  "business_type": "string or null — what kind of business (for website)",
  "sell_online": "true|false or null — whether they want to sell online",
  "agreed_price": "number or null — the price client agreed to"
}}

Only set a field if the client explicitly mentioned it. Do not guess."""
                },
                {"role": "user", "content": conv_text}
            ],
            max_tokens=200,
            response_format={"type": "json_object"}
        )
        # Chunk 5: account this background extraction call against the daily budget.
        _record_openai_cost(extract_model, response)

        details = json.loads(response.choices[0].message.content)

        # Store each confirmed detail
        for key, value in details.items():
            if value is not None:
                update_details(phone, key, value)

        # Also write agreed_price to conversation root if found
        if details.get("agreed_price"):
            conv = load_conversation(phone)
            conv["agreed_price"] = details["agreed_price"]
            save_conversation(phone, conv)

    except Exception as e:
        log.warning("core.detail_extraction_error", error=str(e))


def process_owner_command(command: str) -> str:
    """
    Handle Owner private commands:
    - Price updates
    - Reply style changes
    - Block categories
    """
    command_lower = command.lower()

    # Price update detection
    if any(word in command_lower for word in ["change", "update", "set", "pricing", "price", "₹"]):
        return _handle_price_update(command)

    # Reply style
    if "reply like this" in command_lower:
        settings = load_settings()
        settings["learned_behaviors"][command] = True
        with open(SETTINGS_PATH, "w") as f:
            json.dump(settings, f, indent=2)
        return "Got it. I've saved this reply style and will apply it in similar situations."

    # Block category
    if "don't answer" in command_lower or "ignore" in command_lower:
        settings = load_settings()
        settings["blocked_categories"].append(command)
        with open(SETTINGS_PATH, "w") as f:
            json.dump(settings, f, indent=2)
        return "Understood. I'll avoid responding to that category."

    return "Command noted. What would you like me to do?"


def _handle_price_update(command: str) -> str:
    """Parse and apply price update from Owner command."""
    pricing = load_pricing()

    response = client.chat.completions.create(
        model=_get_reply_model_sync(),
        messages=[
            {
                "role": "system",
                "content": """Extract price update from owner command. Return JSON:
{
  "service": "logo" | "packaging_pouch" | "packaging_box" | "packaging_label" | "website_starter" | "website_business" | "website_premium" | "website_ecommerce",
  "type": "master" | "variant" | "size_change" | "package" | "price_min" | "price_max",
  "new_price": number
}
If unclear, return {"error": "unclear"}"""
            },
            {"role": "user", "content": command}
        ],
        max_tokens=100,
        response_format={"type": "json_object"}
    )

    try:
        update = json.loads(response.choices[0].message.content)
        if "error" in update:
            return "I couldn't understand the price update. Please specify like: 'Change logo price to ₹2500'"

        # Apply update to pricing.json
        if update["service"] == "logo":
            pricing["logo"]["logo_package"]["price"] = update["new_price"]
        elif update["service"] == "packaging_pouch":
            pricing["packaging"]["pouch"][update["type"]]["price"] = update["new_price"]
        elif update["service"] == "packaging_box":
            pricing["packaging"]["box"][update["type"]]["price"] = update["new_price"]
        elif update["service"] == "packaging_label":
            pricing["packaging"]["label"][update["type"]]["price"] = update["new_price"]
        elif update["service"] == "website_starter":
            field = update.get("type", "price_min")
            pricing["website"]["starter"][field] = update["new_price"]
        elif update["service"] == "website_business":
            field = update.get("type", "price_min")
            pricing["website"]["business"][field] = update["new_price"]
        elif update["service"] == "website_premium":
            field = update.get("type", "price_min")
            pricing["website"]["premium"][field] = update["new_price"]
        elif update["service"] == "website_ecommerce":
            field = update.get("type", "price_min")
            pricing["website"]["ecommerce"][field] = update["new_price"]

        with open(PRICING_PATH, "w") as f:
            json.dump(pricing, f, indent=2)

        return f"Price updated successfully. New price for {update['service']} is ₹{update['new_price']}. This applies to all future conversations."

    except Exception as e:
        return f"Error updating price: {str(e)}. Please try again."
