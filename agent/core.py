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
                '"llmReplyModel", "llmVisionModel", "llmReplyTemperature", "toolCallCap" '
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

    system_with_context = (await _load_master_prompt()) + pricing_context + custom_ctx + kb_ctx + f"""
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
    response = client.chat.completions.create(
        model=_get_reply_model_sync(),
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

    # Seriousness: quick replies = +5
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

    # Phase 3: call with tools (strict=true), parallel_tool_calls=false
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        tools=TOOLS,
        parallel_tool_calls=False,
        max_tokens=600,
        temperature=reply_temperature,
    )

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
            # Get next response
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOLS,
                parallel_tool_calls=False,
                max_tokens=600,
                temperature=reply_temperature,
            )
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
    _update_stage_from_reply(phone, reply, message)

    # Extract and store structured client details silently
    _extract_and_store_details(phone)

    return reply


def _update_stage_from_reply(phone: str, reply: str, user_msg: str):
    """Auto-detect and update conversation stage based on reply content."""
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

    # Seriousness updates from user message
    agreement_words = ["okay", "ok", "yes", "sure", "agreed", "fine", "deal", "proceed", "haan", "theek", "chalega"]
    if any(word in user_lower for word in agreement_words):
        update_seriousness(phone, 10)

    rejection_words = ["no", "nahi", "nope", "not interested", "too expensive", "bahut zyada"]
    if any(word in user_lower for word in rejection_words):
        update_seriousness(phone, -5)


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
        response = client.chat.completions.create(
            model=_get_reply_model_sync(),
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
