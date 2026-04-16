"""
Conversation State Manager
Tracks every client conversation by phone number.
Persists to JSON files in data/conversations/

Phase 1: Also dual-writes to Postgres (BotConversation + BotMessage)
via modules.db. JSON remains source of truth.
"""

import json
import os
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

IST = ZoneInfo("Asia/Kolkata")

def _now_ist() -> str:
    """Return current IST time as ISO string."""
    return datetime.now(IST).isoformat()
from pathlib import Path

from modules.logging_config import get_logger
from modules.secrets_manager import encrypt_conversation_data, decrypt_conversation_data

log = get_logger("saransh.agent.conversation")

CONVERSATIONS_DIR = Path("data/conversations")
CONVERSATIONS_DIR.mkdir(parents=True, exist_ok=True)


class ConversationStage:
    NEW = "new"
    IDENTIFYING_SERVICE = "identifying_service"
    COLLECTING_DETAILS = "collecting_details"
    CONFIRMING_DETAILS = "confirming_details"
    PRESENTING_PRICING = "presenting_pricing"
    HANDLING_OBJECTION = "handling_objection"
    NEGOTIATING = "negotiating"
    PRICING_CONFIRMED = "pricing_confirmed"
    HANDOFF = "handoff"
    ESCALATED = "escalated"
    CLOSED = "closed"  # After follow-up exhausted — fresh start on next message


class ServiceType:
    WEBSITE_WHATSAPP = "website_whatsapp"
    LEAD_AUTOMATION = "lead_automation"
    CUSTOM_DASHBOARD = "custom_dashboard"
    CUSTOM_AUTOMATIONS = "custom_automations"
    UNKNOWN = "unknown"


def _get_path(phone: str) -> Path:
    return CONVERSATIONS_DIR / f"{phone}.json"


def load_conversation(phone: str) -> dict:
    path = _get_path(phone)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            conv = json.load(f)
        conv = decrypt_conversation_data(conv)
        # Auto-reset if last message was more than 3 days ago
        last = conv.get("last_updated")
        if last and conv.get("messages"):
            try:
                from dateutil.parser import parse as parse_dt
                last_dt = parse_dt(last)
                now_dt = datetime.now(IST)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=IST)
                days_inactive = (now_dt - last_dt).total_seconds() / 86400
                if days_inactive >= 3:
                    # Archive old conversation, start fresh
                    archive_dir = CONVERSATIONS_DIR / "archive"
                    archive_dir.mkdir(exist_ok=True)
                    archive_name = f"{phone}_{last_dt.strftime('%Y%m%d_%H%M%S')}.json"
                    with open(archive_dir / archive_name, "w", encoding="utf-8") as af:
                        json.dump(conv, af, indent=2, ensure_ascii=False)
                    fresh = _new_conversation(phone)
                    save_conversation(phone, fresh)
                    return fresh
            except Exception:
                pass  # If date parsing fails, just use the conversation as-is
        return conv
    return _new_conversation(phone)


def save_conversation(phone: str, data: dict):
    data["last_updated"] = _now_ist()
    data = encrypt_conversation_data(data)
    path = _get_path(phone)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    # Phase 1: fire-and-forget Postgres sync
    _fire_pg_sync(phone, data)


def _fire_pg_sync(phone: str, conv: dict, direction: str = "INBOUND"):
    """Schedule async Postgres sync without blocking the sync caller."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_pg_sync_safe(phone, conv, direction))
    except RuntimeError:
        pass  # No running loop (sync test code) — skip


async def _pg_sync_safe(phone: str, conv: dict, direction: str = "INBOUND"):
    """Wrap Postgres sync so errors never bubble up."""
    try:
        from modules.db import sync_conversation_to_pg
        await sync_conversation_to_pg(phone, conv, direction)
    except Exception as e:
        log.warning("conversation.pg_sync_error", error=str(e))


def _new_conversation(phone: str) -> dict:
    return {
        "phone": phone,
        "stage": ConversationStage.NEW,
        "service": ServiceType.UNKNOWN,
        "created_at": _now_ist(),
        "last_updated": _now_ist(),
        "messages": [],
        "collected_details": {},
        "seriousness_score": 0,
        "images_received": [],
        "agreed_price": None,
        "negotiation_count": 0,
        "handoff_triggered": False,
        "escalated": False,
        "cross_sell_opportunities": [],
        "notes": [],
        "projects": [],
        "active_project": 0
    }


def add_message(phone: str, role: str, content: str, image_url: Optional[str] = None, wamid: Optional[str] = None):
    """Add a message to conversation history. role: 'user' or 'assistant'"""
    conv = load_conversation(phone)
    msg = {
        "role": role,
        "content": content,
        "timestamp": _now_ist()
    }
    if image_url:
        msg["image_url"] = image_url
    if wamid:
        msg["wamid"] = wamid
    conv["messages"].append(msg)
    # Keep last 30 messages to manage token usage
    if len(conv["messages"]) > 30:
        conv["messages"] = conv["messages"][-30:]
    save_conversation(phone, conv)

    # Phase 1: dual-write message to Postgres
    _fire_pg_message(phone, role, content, wamid=wamid, image_url=image_url)


def _fire_pg_message(phone: str, role: str, content: str, wamid: Optional[str] = None, image_url: Optional[str] = None):
    """Schedule async BotMessage insert without blocking."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_pg_message_safe(phone, role, content, wamid=wamid, image_url=image_url))
    except RuntimeError:
        pass


async def _pg_message_safe(phone: str, role: str, content: str, wamid: Optional[str] = None, image_url: Optional[str] = None):
    """Wrap message insert so errors never bubble up."""
    try:
        from modules.db import sync_message_to_pg
        media_type = "image" if image_url else None
        await sync_message_to_pg(
            phone=phone, role=role, content=content,
            wamid=wamid, media_type=media_type, media_url=image_url,
        )
    except Exception as e:
        log.warning("conversation.pg_message_error", error=str(e))


def update_stage(phone: str, stage: str):
    conv = load_conversation(phone)
    conv["stage"] = stage
    save_conversation(phone, conv)


def update_service(phone: str, service: str):
    conv = load_conversation(phone)
    conv["service"] = service
    save_conversation(phone, conv)


def update_details(phone: str, key: str, value):
    conv = load_conversation(phone)
    conv["collected_details"][key] = value
    save_conversation(phone, conv)


def update_seriousness(phone: str, delta: int):
    conv = load_conversation(phone)
    conv["seriousness_score"] = max(0, min(100, conv["seriousness_score"] + delta))
    save_conversation(phone, conv)


def add_image(phone: str, image_url: str, caption: str = "", tag: str = "reference"):
    conv = load_conversation(phone)
    conv["images_received"].append({
        "url": image_url,
        "caption": caption,
        "tag": tag,  # "reference" | "existing_logo" | "sample_request"
        "timestamp": _now_ist()
    })
    save_conversation(phone, conv)


def add_project(phone: str, service: str) -> int:
    """Add a new project for a multi-service client. Returns new project index."""
    conv = load_conversation(phone)
    project = {
        "id": len(conv.get("projects", [])) + 1,
        "service": service,
        "details": {},
        "stage": "collecting",
        "agreed_price": None,
        "notes": []
    }
    if "projects" not in conv:
        conv["projects"] = []
    conv["projects"].append(project)
    conv["active_project"] = len(conv["projects"]) - 1
    save_conversation(phone, conv)
    return conv["active_project"]


def get_projects(phone: str) -> list:
    conv = load_conversation(phone)
    return conv.get("projects", [])


def update_project_details(phone: str, project_index: int, key: str, value):
    conv = load_conversation(phone)
    projects = conv.get("projects", [])
    if project_index < len(projects):
        projects[project_index]["details"][key] = value
        save_conversation(phone, conv)


def add_note(phone: str, note: str):
    conv = load_conversation(phone)
    conv["notes"].append(note)
    save_conversation(phone, conv)


def mark_handoff(phone: str, agreed_price):
    conv = load_conversation(phone)
    conv["stage"] = ConversationStage.HANDOFF
    conv["handoff_triggered"] = True
    conv["agreed_price"] = agreed_price
    save_conversation(phone, conv)


def get_recent_messages(phone: str, count: int = 10) -> list:
    conv = load_conversation(phone)
    return conv["messages"][-count:]


def get_summary(phone: str) -> dict:
    """Get a clean summary of the conversation for owner alerts."""
    conv = load_conversation(phone)
    return {
        "phone": conv["phone"],
        "service": conv["service"],
        "stage": conv["stage"],
        "details": conv["collected_details"],
        "seriousness_score": conv["seriousness_score"],
        "agreed_price": conv["agreed_price"],
        "images_count": len(conv["images_received"]),
        "images_received": conv["images_received"],
        "notes": conv["notes"],
        "projects": conv.get("projects", [])
    }


def reset_conversation(phone: str):
    """Reset conversation (for testing or if client starts fresh)."""
    path = _get_path(phone)
    if path.exists():
        os.remove(path)
