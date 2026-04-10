"""
Conversation State Manager
Tracks every client conversation by phone number.
Persists to JSON files in data/conversations/
"""

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional

IST = ZoneInfo("Asia/Kolkata")

def _now_ist() -> str:
    """Return current IST time as ISO string."""
    return datetime.now(IST).isoformat()
from pathlib import Path

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
    LOGO = "logo"
    PACKAGING = "packaging"
    WEBSITE = "website"
    UNKNOWN = "unknown"


def _get_path(phone: str) -> Path:
    return CONVERSATIONS_DIR / f"{phone}.json"


def load_conversation(phone: str) -> dict:
    path = _get_path(phone)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return _new_conversation(phone)


def save_conversation(phone: str, data: dict):
    data["last_updated"] = _now_ist()
    path = _get_path(phone)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


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
