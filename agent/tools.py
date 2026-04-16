"""
Phase 3 — Structured OpenAI tools for the sales agent.

8 tools as Pydantic v2 models, exported as OpenAI tool schemas
with strict=true and additionalProperties=false.

Lead scoring algorithm (0-100) updates Lead.leadScore on every capture_lead call.
"""

import json
import os
from typing import Optional, Literal
from pydantic import BaseModel, Field

from modules.logging_config import get_logger

log = get_logger("saransh.agent.tools")


# ── Pydantic schemas for tool parameters ──────────────────

class SearchKnowledgeParams(BaseModel):
    query: str = Field(description="Search query for knowledge base")
    limit: int = Field(default=5, description="Max results to return")

class CaptureLeadParams(BaseModel):
    name: Optional[str] = Field(default=None, description="Client's name")
    businessType: Optional[str] = Field(default=None, description="Type of business (e.g. coffee shop, restaurant)")
    specificNeed: str = Field(description="What the client needs (e.g. logo design, packaging)")
    budgetSignal: Optional[str] = Field(default=None, description="Budget indicator (e.g. '4000', 'low budget')")
    timeline: Optional[str] = Field(default=None, description="When they need it (e.g. 'next week', 'urgent')")
    isDecisionMaker: Optional[bool] = Field(default=None, description="Whether the person is the decision maker")
    notes: Optional[str] = Field(default=None, description="Additional notes about the lead")

class EscalateToHumanParams(BaseModel):
    reason: str = Field(description="Why escalation is needed")
    urgency: Literal["low", "medium", "high"] = Field(description="Urgency level")

class SendMediaParams(BaseModel):
    mediaType: Literal["image", "document", "video"] = Field(description="Type of media")
    mediaId: str = Field(description="WhatsApp media ID or URL")
    caption: Optional[str] = Field(default=None, description="Caption for the media")

class MarkOptedOutParams(BaseModel):
    phone: str = Field(description="Phone number to opt out")
    reason: Optional[str] = Field(default=None, description="Reason for opt-out")

class BookAppointmentParams(BaseModel):
    serviceName: str = Field(description="Service being discussed")
    preferredDate: str = Field(description="Preferred date (e.g. 'Monday', '2026-04-20')")
    preferredTime: str = Field(description="Preferred time (e.g. '2pm', 'morning')")
    notes: Optional[str] = Field(default=None, description="Additional notes")

class CheckStatusParams(BaseModel):
    entityType: Literal["order", "quote", "project"] = Field(description="Type of entity")
    entityId: str = Field(description="ID or reference number")

class GetEntityDetailsParams(BaseModel):
    entityType: Literal["service", "package", "portfolio"] = Field(description="Type of entity")
    name: str = Field(description="Name to look up")


# ── OpenAI tool definitions (strict=true) ─────────────────

def _schema_to_strict(model: type[BaseModel]) -> dict:
    """Convert Pydantic model to OpenAI strict tool parameter schema."""
    schema = model.model_json_schema()
    # Remove $defs and title — OpenAI strict mode doesn't want them
    schema.pop("$defs", None)
    schema.pop("title", None)
    # Ensure additionalProperties=false for strict mode
    schema["additionalProperties"] = False
    # For strict mode, all properties must be in required
    # Optional fields use type union with null
    props = schema.get("properties", {})
    all_keys = list(props.keys())
    schema["required"] = all_keys
    for key, prop in props.items():
        # If field has a default of None or is Optional, make it anyOf with null
        if prop.get("default") is None and "anyOf" not in prop and prop.get("type") != "null":
            # Check if it's already nullable
            if "type" in prop:
                original_type = prop.pop("type")
                prop["anyOf"] = [{"type": original_type}, {"type": "null"}]
            prop.pop("default", None)
        elif "default" in prop and prop["default"] is None:
            if "anyOf" not in prop and prop.get("type") != "null":
                if "type" in prop:
                    original_type = prop.pop("type")
                    prop["anyOf"] = [{"type": original_type}, {"type": "null"}]
            prop.pop("default", None)
    return schema


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "Search the knowledge base for answers to client questions about services, processes, or policies.",
            "parameters": _schema_to_strict(SearchKnowledgeParams),
            "strict": True,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "capture_lead",
            "description": "Save or update lead information when a client shares their details, needs, budget, or timeline. Call this whenever new qualifying information is gathered.",
            "parameters": _schema_to_strict(CaptureLeadParams),
            "strict": True,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": "Escalate the conversation to Saransh Sharma sir when the bot cannot handle the request, client is frustrated, or the deal is ready for human follow-up.",
            "parameters": _schema_to_strict(EscalateToHumanParams),
            "strict": True,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_media",
            "description": "Send an image, document, or video to the client via WhatsApp.",
            "parameters": _schema_to_strict(SendMediaParams),
            "strict": True,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "mark_opted_out",
            "description": "Mark a lead as opted out from receiving messages. Use when client explicitly wants to stop communication.",
            "parameters": _schema_to_strict(MarkOptedOutParams),
            "strict": True,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": "Book a call or meeting appointment. Saransh sir will confirm the time.",
            "parameters": _schema_to_strict(BookAppointmentParams),
            "strict": True,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_status",
            "description": "Check the status of an order, quote, or project for the client.",
            "parameters": _schema_to_strict(CheckStatusParams),
            "strict": True,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_entity_details",
            "description": "Get details about a service, package, or portfolio item.",
            "parameters": _schema_to_strict(GetEntityDetailsParams),
            "strict": True,
        },
    },
]


# ── Lead scoring ──────────────────────────────────────────

def compute_lead_score(params: dict) -> int:
    """
    Compute lead score (0-100) based on captured information.
    +10 name, +10 businessType, +15 specificNeed, +20 budgetSignal,
    +15 timeline, +15 isDecisionMaker, +10 waPhone (always true), +5 proactive
    """
    score = 10  # waPhone always verified in our WhatsApp flow
    if params.get("name"):
        score += 10
    if params.get("businessType"):
        score += 10
    if params.get("specificNeed"):
        score += 15
    if params.get("budgetSignal"):
        score += 20
    if params.get("timeline"):
        score += 15
    if params.get("isDecisionMaker") is True:
        score += 15
    if params.get("notes"):
        score += 5  # proactive engagement signal
    return min(score, 100)


def score_bucket(score: int) -> str:
    """Map score to quality bucket."""
    if score >= 86:
        return "READY_FOR_CALL"
    elif score >= 61:
        return "HOT"
    elif score >= 31:
        return "WARM"
    return "COLD"


# ── Tool execution ────────────────────────────────────────

async def execute_tool(tool_name: str, arguments: dict, phone: str) -> str:
    """Execute a tool call and return the result as a string for OpenAI."""

    if tool_name == "search_knowledge":
        return await _exec_search_knowledge(arguments)

    elif tool_name == "capture_lead":
        return await _exec_capture_lead(arguments, phone)

    elif tool_name == "escalate_to_human":
        return await _exec_escalate_to_human(arguments, phone)

    elif tool_name == "send_media":
        return json.dumps({"status": "queued", "note": "Media send will be handled by the reply flow"})

    elif tool_name == "mark_opted_out":
        return await _exec_mark_opted_out(arguments)

    elif tool_name == "book_appointment":
        return await _exec_book_appointment(arguments, phone)

    elif tool_name == "check_status":
        return json.dumps({"status": "pending", "message": "Saransh sir will confirm the status and get back to you shortly."})

    elif tool_name == "get_entity_details":
        return await _exec_get_entity_details(arguments)

    return json.dumps({"error": f"Unknown tool: {tool_name}"})


async def _exec_search_knowledge(args: dict) -> str:
    """Search config/settings.json knowledge_base entries."""
    try:
        import os
        settings_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "settings.json")
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
        kb = settings.get("knowledge_base", [])
        query_lower = args.get("query", "").lower()
        limit = args.get("limit", 5)
        # Simple keyword matching
        results = []
        for entry in kb:
            q = entry.get("question", "").lower()
            a = entry.get("answer", "")
            if any(word in q for word in query_lower.split()):
                results.append({"question": entry["question"], "answer": a})
        results = results[:limit]
        if not results:
            return json.dumps({"results": [], "note": "No matching knowledge base entries found. Use your training knowledge."})
        return json.dumps({"results": results})
    except Exception as e:
        log.warning("tools.search_knowledge_error", error=str(e))
        return json.dumps({"results": [], "error": str(e)})


async def _exec_capture_lead(args: dict, phone: str) -> str:
    """Capture/update lead info in Postgres and compute score."""
    score = compute_lead_score(args)
    bucket = score_bucket(score)

    log.info("tools.capture_lead",
             phone_len=len(phone), score=score, bucket=bucket,
             has_name=bool(args.get("name")),
             has_budget=bool(args.get("budgetSignal")),
             has_timeline=bool(args.get("timeline")))

    # Update Lead in Postgres (if row exists) + BotConversation.seriousnessScore
    try:
        from modules.db import _pool, _pool_ok, _utcnow
        if _pool_ok():
            async with _pool.acquire() as conn:
                now = _utcnow()
                # Update Lead row if it exists
                lead_row = await conn.fetchrow(
                    'SELECT id FROM "Lead" WHERE "waPhone" = $1', phone
                )
                if lead_row:
                    lead_id = lead_row["id"]
                    update_parts = ['"lastInteractionAt" = $1', '"updatedAt" = $1']
                    update_vals = [now]
                    idx = 2
                    if args.get("name"):
                        update_parts.append(f'"name" = ${idx}')
                        update_vals.append(args["name"])
                        idx += 1
                    if args.get("notes"):
                        update_parts.append(f'"notes" = ${idx}')
                        update_vals.append(args["notes"])
                        idx += 1
                    if args.get("specificNeed"):
                        update_parts.append(f'"serviceInterest" = ${idx}')
                        update_vals.append(args["specificNeed"])
                        idx += 1
                    if args.get("budgetSignal"):
                        update_parts.append(f'"budget" = ${idx}')
                        update_vals.append(args["budgetSignal"])
                        idx += 1
                    if args.get("timeline"):
                        update_parts.append(f'"timeline" = ${idx}')
                        update_vals.append(args["timeline"])
                        idx += 1
                    update_vals.append(lead_id)
                    await conn.execute(
                        f'UPDATE "Lead" SET {", ".join(update_parts)} WHERE id = ${idx}',
                        *update_vals,
                    )

                # Update BotConversation.seriousnessScore (always, even without Lead)
                bc_row = await conn.fetchrow(
                    'SELECT id, "seriousnessScore" FROM "BotConversation" WHERE "waPhone" = $1 ORDER BY "createdAt" DESC LIMIT 1',
                    phone,
                )
                if bc_row:
                    existing = bc_row["seriousnessScore"] or 0
                    new_score = max(existing, score)
                    await conn.execute(
                        'UPDATE "BotConversation" SET "seriousnessScore" = $1, "updatedAt" = $2 WHERE id = $3',
                        new_score, now, bc_row["id"],
                    )
                    score = new_score
    except Exception as e:
        log.warning("tools.capture_lead_db_error", error=str(e))

    # Update conversation JSON too
    try:
        from agent.conversation import load_conversation, save_conversation
        conv = load_conversation(phone)
        conv["seriousness_score"] = score
        if args.get("name"):
            conv["collected_details"]["name"] = args["name"]
        if args.get("businessType"):
            conv["collected_details"]["business_type"] = args["businessType"]
        if args.get("specificNeed"):
            conv["collected_details"]["specific_need"] = args["specificNeed"]
        if args.get("budgetSignal"):
            conv["collected_details"]["budget"] = args["budgetSignal"]
        if args.get("timeline"):
            conv["collected_details"]["timeline"] = args["timeline"]
        if args.get("isDecisionMaker") is not None:
            conv["collected_details"]["is_decision_maker"] = args["isDecisionMaker"]
        save_conversation(phone, conv)
    except Exception as e:
        log.warning("tools.capture_lead_conv_error", error=str(e))

    result = {
        "status": "captured",
        "leadScore": score,
        "bucket": bucket,
        "message": f"Lead info saved. Score: {score}/100 ({bucket})"
    }

    # Auto-escalate if READY_FOR_CALL — server-side, not relying on LLM
    if bucket == "READY_FOR_CALL":
        result["auto_escalation"] = "Score >= 86 — automatic Telegram escalation triggered"
        log.info("tools.auto_escalate", score=score, phone_len=len(phone))
        try:
            escalation_result = await _exec_escalate_to_human(
                {"reason": f"Auto-escalation: lead score {score}/100 (READY_FOR_CALL)", "urgency": "high"},
                phone,
            )
            import json as _json
            esc_data = _json.loads(escalation_result)
            result["telegram_sent"] = esc_data.get("telegram_sent", False)
            log.info("tools.auto_escalate_done", telegram_sent=result["telegram_sent"])
        except Exception as e:
            log.error("tools.auto_escalate_failed", error=str(e))

    return json.dumps(result)


async def _exec_escalate_to_human(args: dict, phone: str) -> str:
    """Trigger escalation alert via Telegram + AuditLog."""
    reason = args.get("reason", "No reason provided")
    urgency = args.get("urgency", "medium")
    log.info("tools.escalate", reason=reason, urgency=urgency, phone_len=len(phone))

    # Write audit log
    try:
        from modules.db import audit_log
        await audit_log("bot", "escalate_to_human", "Lead", entity_id=phone,
                        after_json={"reason": reason, "urgency": urgency})
    except Exception:
        pass

    # Send Telegram alert using existing infrastructure
    telegram_ok = False
    try:
        from agent.conversation import load_conversation, get_summary
        summary = get_summary(phone)
        from agent.whatsapp import send_escalation_alert
        await send_escalation_alert(
            phone=phone,
            client_question=reason,
            service=summary.get("service", "unknown"),
            budget=str(summary.get("details", {}).get("budget", "N/A")),
            timeline=str(summary.get("details", {}).get("timeline", "N/A")),
        )
        telegram_ok = True
        log.info("tools.escalate_telegram_sent", phone_len=len(phone), urgency=urgency)
    except Exception as e:
        log.error("tools.escalate_telegram_failed", error=str(e), phone_len=len(phone))

    return json.dumps({
        "status": "escalated",
        "telegram_sent": telegram_ok,
        "message": f"Escalation triggered ({urgency} urgency). Saransh sir has been notified via Telegram."
    })


async def _exec_mark_opted_out(args: dict) -> str:
    """LLM-initiated opt-out."""
    phone = args.get("phone", "")
    try:
        from modules.db import set_lead_opted_out, audit_log
        await set_lead_opted_out(phone, True)
        await audit_log("bot", "opt_out_via_tool", "Lead", entity_id=phone,
                        after_json={"reason": args.get("reason")})
    except Exception as e:
        log.warning("tools.mark_opted_out_error", error=str(e))

    return json.dumps({"status": "opted_out", "phone": phone})


async def _exec_book_appointment(args: dict, phone: str) -> str:
    """Book appointment — creates a note and alerts owner."""
    log.info("tools.book_appointment",
             service=args.get("serviceName"),
             date=args.get("preferredDate"),
             time=args.get("preferredTime"),
             phone_len=len(phone))

    try:
        from agent.conversation import add_note
        note = f"Appointment requested: {args.get('serviceName')} on {args.get('preferredDate')} at {args.get('preferredTime')}"
        if args.get("notes"):
            note += f" — {args['notes']}"
        add_note(phone, note)
    except Exception:
        pass

    try:
        from modules.db import audit_log
        await audit_log("bot", "book_appointment", "Lead", entity_id=phone, after_json=args)
    except Exception:
        pass

    return json.dumps({
        "status": "booked",
        "message": f"Appointment request noted. Saransh sir will confirm {args.get('preferredDate')} {args.get('preferredTime')} availability."
    })


async def _exec_get_entity_details(args: dict) -> str:
    """Get details about services/packages/portfolio."""
    entity_type = args.get("entityType", "")
    name = args.get("name", "").lower()

    if entity_type == "service":
        # Try to load from settings
        try:
            settings_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "settings.json")
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
            # Search in custom instructions or knowledge base
            kb = settings.get("knowledge_base", [])
            for entry in kb:
                if name in entry.get("question", "").lower():
                    return json.dumps({"found": True, "details": entry["answer"]})
        except Exception:
            pass
        return json.dumps({"found": False, "message": "Use your training knowledge to describe this service."})

    return json.dumps({"found": False, "message": f"Details for {entity_type} '{name}' — Saransh sir will provide specifics."})
