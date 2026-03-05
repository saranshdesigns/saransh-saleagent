"""
SaranshDesigns AI Sales Agent
Main FastAPI server — handles Meta WhatsApp webhooks + Dashboard
"""

import os
import json
import asyncio
import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

_IST = ZoneInfo("Asia/Kolkata")
def _now_ist_iso() -> str:
    return datetime.datetime.now(_IST).isoformat()
from fastapi import FastAPI, Request, Response, HTTPException, Depends
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.websockets import WebSocket, WebSocketDisconnect
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.base import JobLookupError

from agent.core import process_message, process_owner_command
from agent.conversation import load_conversation, get_summary, mark_handoff, add_image, add_message
from agent.whatsapp import (
    send_text, send_image, send_portfolio_samples,
    send_owner_alert, send_escalation_alert,
    download_media, encode_image_to_base64
)
from agent.portfolio import get_samples
from agent.dashboard_auth import verify_password, create_access_token, require_auth

load_dotenv()

app = FastAPI(title="SaranshDesigns AI Agent", version="1.0.0")

# Background scheduler for follow-up messages
scheduler = AsyncIOScheduler()


@app.on_event("startup")
async def startup_event():
    scheduler.start()
    print("Scheduler started")


@app.on_event("shutdown")
async def shutdown_event():
    scheduler.shutdown()

VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "saranshdesigns_webhook_2024")
OWNER_PHONE = os.getenv("OWNER_PHONE", "918850069662")

# Serve dashboard static files
_dashboard_path = Path("dashboard")
if _dashboard_path.exists():
    app.mount("/dashboard", StaticFiles(directory="dashboard", html=True), name="dashboard")


# ============================================================
# WEBSOCKET CONNECTION MANAGER
# ============================================================

class DashboardConnectionManager:
    def __init__(self):
        self.active_connections: list = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, data: dict):
        """Broadcast to all connected dashboard clients. Remove dead connections."""
        dead = []
        for conn in self.active_connections:
            try:
                await conn.send_json(data)
            except Exception:
                dead.append(conn)
        for d in dead:
            self.disconnect(d)


ws_manager = DashboardConnectionManager()


# ============================================================
# WEBHOOK VERIFICATION (Meta requires this on setup)
# ============================================================

@app.get("/webhook")
async def verify_webhook(request: Request):
    params = dict(request.query_params)
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("✅ Webhook verified successfully")
        return PlainTextResponse(content=challenge)
    else:
        raise HTTPException(status_code=403, detail="Verification failed")


# ============================================================
# INCOMING MESSAGES HANDLER
# ============================================================

@app.post("/webhook")
async def receive_message(request: Request):
    body = await request.json()
    print(f"\n📩 Webhook received: {json.dumps(body, indent=2)[:500]}")

    try:
        entry = body.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])

        if not messages:
            print("ℹ️ No messages in payload (status update or other event)")
            return Response(status_code=200)

        for message in messages:
            phone = message.get("from", "")
            msg_type = message.get("type", "")
            print(f"📱 Message from: {phone} | Type: {msg_type}")

            if not phone:
                continue

            # Owner commands
            if phone == OWNER_PHONE:
                print(f"👑 Owner message detected")
                if msg_type == "text":
                    text = message.get("text", {}).get("body", "")
                    reply = process_owner_command(text)
                    await send_text(phone, reply)
                continue

            # Client messages
            print(f"🔄 Processing client message from {phone}...")
            asyncio.create_task(handle_client_message(phone, message, msg_type))

    except Exception as e:
        print(f"❌ Error processing webhook: {e}")
        import traceback
        traceback.print_exc()

    return Response(status_code=200)


# ============================================================
# FOLLOW-UP SCHEDULER
# ============================================================

def _schedule_followup(phone: str, hours: float, is_final: bool = False):
    """Schedule a follow-up message for this phone number."""
    job_id = f"followup_{'final' if is_final else '6h'}_{phone}"
    try:
        scheduler.remove_job(job_id)
    except JobLookupError:
        pass
    func = _send_final_followup if is_final else _send_first_followup
    run_at = datetime.datetime.now() + datetime.timedelta(hours=hours)
    scheduler.add_job(func, "date", run_date=run_at, args=[phone], id=job_id)


def _cancel_followups(phone: str):
    """Cancel all pending follow-ups when client responds."""
    for suffix in ["6h", "final", "portfolio"]:
        try:
            scheduler.remove_job(f"followup_{suffix}_{phone}")
        except JobLookupError:
            pass


def _schedule_portfolio_followup(phone: str):
    """Schedule a 5-minute follow-up after portfolio is sent."""
    job_id = f"followup_portfolio_{phone}"
    try:
        scheduler.remove_job(job_id)
    except JobLookupError:
        pass
    run_at = datetime.datetime.now() + datetime.timedelta(minutes=5)
    scheduler.add_job(_send_portfolio_followup, "date", run_date=run_at, args=[phone], id=job_id)


async def _send_portfolio_followup(phone: str):
    """5-min follow-up after portfolio send — nudge client to continue."""
    from agent.conversation import load_conversation, add_message, ConversationStage
    conv = load_conversation(phone)
    if conv.get("stage") in [ConversationStage.HANDOFF, ConversationStage.CLOSED]:
        return
    # Only send if client hasn't replied since portfolio was sent
    messages = conv.get("messages", [])
    if messages and messages[-1].get("role") == "user":
        return  # Client already replied, no need to nudge
    msg = "Did you get a chance to check the samples? Would love to know your thoughts — happy to answer any questions or show more work! 😊"
    await send_text(phone, msg)
    add_message(phone, "assistant", msg)


async def _send_first_followup(phone: str):
    """6-hour follow-up — polite check-in."""
    from agent.conversation import load_conversation, add_message, ConversationStage
    conv = load_conversation(phone)
    # Don't send if already at handoff/closed or no messages
    if conv.get("stage") in [ConversationStage.HANDOFF, ConversationStage.CLOSED, "new"] or not conv.get("messages"):
        return
    msg = "Hi! Just following up — are you still interested in our services? Happy to help whenever you're ready. 🙂"
    await send_text(phone, msg)
    add_message(phone, "assistant", msg)
    # Schedule the final 24h follow-up
    _schedule_followup(phone, hours=24, is_final=True)


async def _send_final_followup(phone: str):
    """24-hour final follow-up — then close conversation."""
    from agent.conversation import load_conversation, add_message, update_stage, ConversationStage
    conv = load_conversation(phone)
    if conv.get("stage") in [ConversationStage.HANDOFF, ConversationStage.CLOSED, "new"] or not conv.get("messages"):
        return
    msg = "Hi! This is our last follow-up. If you'd ever like to work with us in the future, feel free to reach out anytime. Best wishes! 🙏"
    await send_text(phone, msg)
    add_message(phone, "assistant", msg)
    # Mark as closed — next fresh message will start a new conversation
    update_stage(phone, ConversationStage.CLOSED)


async def handle_client_message(phone: str, message: dict, msg_type: str):
    """Handle client message asynchronously."""
    try:
        image_data = None
        text = ""
        print(f"⚙️ Handling message from {phone}...")

        # --- CONTEXT REVIVAL & CLOSED STAGE CHECK ---
        from agent.conversation import ConversationStage as CS
        _conv_check = load_conversation(phone)
        _stage = _conv_check.get("stage", "new")
        _last_updated = _conv_check.get("last_updated", "")

        if _stage == CS.HANDOFF:
            # After handoff: if < 24 hours, remind them owner will contact
            # If >= 24 hours, reset and start fresh
            try:
                _last_time = datetime.datetime.fromisoformat(_last_updated)
                _hours_elapsed = (datetime.datetime.now() - _last_time).total_seconds() / 3600
                if _hours_elapsed < 24:
                    await send_text(phone, "Hi! Your enquiry has already been noted and Saransh Sir will be in touch with you shortly. Please wait for his message!")
                    return
                else:
                    # Fresh start after 24h post-handoff
                    from agent.conversation import reset_conversation
                    reset_conversation(phone)
            except Exception:
                pass

        elif _stage == CS.CLOSED:
            # Closed after follow-ups exhausted → always fresh start
            from agent.conversation import reset_conversation
            reset_conversation(phone)

        # Cancel any pending follow-ups since client is now active
        _cancel_followups(phone)

        if msg_type == "text":
            text = message.get("text", {}).get("body", "").strip()

        elif msg_type == "image":
            media_id = message.get("image", {}).get("id", "")
            caption = message.get("image", {}).get("caption", "")
            text = caption or "I've sent you a reference image."

            image_bytes = await download_media(media_id)
            if image_bytes:
                image_data = encode_image_to_base64(image_bytes)
                # Tag as existing_logo if client mentioned logo redesign/improvement
                _caption_lower = (caption or "").lower()
                _conv_check2 = load_conversation(phone)
                _recent_msgs = " ".join(
                    m.get("content", "") for m in _conv_check2.get("messages", [])[-6:]
                ).lower()
                _is_existing_logo = any(kw in _caption_lower + _recent_msgs for kw in [
                    "improve", "redesign", "better", "existing logo", "already have", "logo hai", "logo bana do"
                ])
                _img_tag = "existing_logo" if _is_existing_logo else "reference"
                add_image(phone, f"media_{media_id}", caption, tag=_img_tag)

        elif msg_type == "document":
            text = "I've shared a document/file."

        elif msg_type == "audio":
            await send_text(phone, "Thanks! I'm text-based, so please type your message and I'll be happy to help.")
            return

        else:
            text = f"Received {msg_type} message."

        if not text and not image_data:
            return

        # Broadcast incoming client message to dashboard
        await ws_manager.broadcast({
            "type": "new_message",
            "phone": phone,
            "role": "user",
            "content": text,
            "timestamp": _now_ist_iso()
        })

        # Greeting — split into 2 messages with 4s delay (more human feel)
        from agent.core import _get_ist_greeting
        _GREETINGS = {"hello", "hi", "hey", "hii", "helo", "hellow", "namaste", "namaskar", "sup", "yo", "hai", "hii"}
        conv_state = load_conversation(phone)
        if conv_state.get("stage") == "new" and text.lower().strip() in _GREETINGS:
            _greet = _get_ist_greeting()
            msg1 = f"{_greet} Welcome to SaranshDesigns. How can I assist you today?"
            msg2 = "Are you looking for logo design, packaging design, or website design?"
            await send_text(phone, msg1)
            await asyncio.sleep(4)
            await send_text(phone, msg2)
            add_message(phone, "user", text)
            add_message(phone, "assistant", msg1 + " " + msg2)
            return

        # Call request — trigger owner alert immediately
        lower = text.lower()
        call_keywords = ["call me", "phone call", "call karo", "call karein", "baat karni hai", "talk on call", "speak on call", "can we call", "phone pe baat"]
        if any(kw in lower for kw in call_keywords):
            reply = "Sure, I will arrange a call for you. Please wait, I'll coordinate with Saransh Sir and you will receive a call shortly."
            await send_text(phone, reply)
            add_message(phone, "user", text)
            add_message(phone, "assistant", reply)
            await send_owner_alert(get_summary(phone))
            return

        # Portfolio/sample request
        if any(word in lower for word in ["sample", "portfolio", "work", "previous work", "examples", "show me"]):
            await handle_portfolio_request(phone, text)
            return

        # Standard AI processing
        print(f"🤖 Sending to OpenAI for processing...")
        reply = process_message(phone, text, image_data)
        print(f"✅ AI reply generated: {reply[:100]}...")
        print(f"📤 Sending reply to {phone}...")
        result = await send_text(phone, reply)
        print(f"📬 WhatsApp API response: {result}")

        # Broadcast AI reply to dashboard
        conv_state = load_conversation(phone)
        await ws_manager.broadcast({
            "type": "new_message",
            "phone": phone,
            "role": "assistant",
            "content": reply,
            "timestamp": _now_ist_iso(),
            "stage": conv_state.get("stage"),
            "service": conv_state.get("service"),
            "seriousness_score": conv_state.get("seriousness_score", 0)
        })

        # Owner handoff — detected from AI reply phrase (multiple phrasings)
        _handoff_phrases = [
            "owner will message you shortly",
            "he will message you shortly",
            "will message you shortly to proceed",
            "connect you with saransh sharma sir",
            "connect you with the owner directly",
            "saransh sharma sir will message",
        ]
        if any(p in reply.lower() for p in _handoff_phrases):
            summary = get_summary(phone)
            mark_handoff(phone, summary.get("agreed_price"))
            _cancel_followups(phone)
            await send_owner_alert(summary)

        # Schedule 6h follow-up (only if conversation is still active, not at handoff)
        _fresh_conv = load_conversation(phone)
        if _fresh_conv.get("stage") not in [CS.HANDOFF, CS.CLOSED]:
            _schedule_followup(phone, hours=6, is_final=False)

        # Escalation alert
        if "owner alert" in reply.lower():
            summary = get_summary(phone)
            await send_escalation_alert(
                phone=phone,
                client_question=text,
                service=summary.get("service", "unknown"),
                budget=str(summary["details"].get("budget", "N/A")),
                timeline=str(summary["details"].get("timeline", "N/A"))
            )

    except Exception as e:
        print(f"❌ Error handling client message from {phone}: {e}")
        import traceback
        traceback.print_exc()


PORTFOLIO_LINKS = (
    "Check out the full portfolio here:\n"
    "🌐 https://saransh.space/\n"
    "🎨 https://www.behance.net/SaranshDesigns\n"
    "📸 https://www.instagram.com/saranshdesigns"
)


def _extract_service_from_text(text: str) -> str:
    """Detect which service the user is requesting samples for in the current message."""
    lower = text.lower()
    if any(w in lower for w in ["logo", "brand logo", "logomark", "wordmark", "icon"]):
        return "logo"
    if any(w in lower for w in ["packaging", "pouch", "packet", "box", "label", "sachet", "jar", "bottle", "wrapper"]):
        return "packaging"
    if any(w in lower for w in ["website", "web design", "site", "webpage", "landing page"]):
        return "website"
    return None


async def handle_portfolio_request(phone: str, text: str):
    """
    Handle sample/portfolio requests.
    Detects service from CURRENT MESSAGE first (handles cross-service requests like
    'show me logo samples' during a packaging conversation), then falls back to stored service.
    Fetches images from Google Drive and sends portfolio links after images.
    """
    conv = load_conversation(phone)
    # Detect service from current message first — handles cross-service sample requests
    service = _extract_service_from_text(text) or conv.get("service") or "logo"
    details = conv.get("collected_details", {})

    # Extract packaging type from stored details or conversation messages
    packaging_type = (
        details.get("packaging_type") or
        details.get("type") or
        details.get("packaging") or
        _extract_packaging_type_from_text(text) or
        _extract_packaging_type_from_text(_get_recent_text(conv))
    )

    # Extract product/business category from stored details, current message, or conversation
    raw_category = (
        details.get("product") or
        details.get("product_name") or
        details.get("business_category") or
        details.get("category") or
        text
    )
    category = _extract_category_from_text(raw_category) or _extract_category_from_text(_get_recent_text(conv))

    result = get_samples(service, category, packaging_type)

    add_message(phone, "user", text)

    if result["files"]:
        await send_text(phone, result["message"])
        for img_path in result["files"]:
            await send_image(phone, str(img_path))
        follow_up = f"Would you like something similar or a completely fresh concept?\n\n{PORTFOLIO_LINKS}"
        await send_text(phone, follow_up)
        add_message(phone, "assistant", result["message"] + "\n[images sent]\n" + follow_up)
    else:
        reply = f"{result['message']}\n\n{PORTFOLIO_LINKS}\n\nWould you like something similar or a completely fresh concept?"
        await send_text(phone, reply)
        add_message(phone, "assistant", reply)

    # Schedule 5-min follow-up after portfolio — if client doesn't reply, nudge them
    _schedule_portfolio_followup(phone)


async def trigger_handoff(phone: str):
    """Trigger Owner handoff — notify both client and owner."""
    summary = get_summary(phone)
    mark_handoff(phone, summary.get("agreed_price"))
    client_msg = "Great. I'll now connect you with the Owner directly. The Owner will message you shortly to proceed with the advance and project initiation."
    await send_text(phone, client_msg)
    await send_owner_alert(summary)


def _is_handoff_confirmation(text: str) -> bool:
    keywords = ["yes", "okay", "ok", "proceed", "agreed", "sure", "haan", "theek", "chalega", "connect me", "let's go"]
    return any(w in text.lower() for w in keywords)


def _detect_agreement(text: str) -> bool:
    keywords = ["yes", "ok", "okay", "agreed", "proceed", "sure", "haan", "done", "let's start", "confirmed"]
    return any(w in text.lower() for w in keywords)


def _extract_category_from_text(text: str) -> str:
    """Extract product/business category from text. Returns category name or None."""
    if not text:
        return None
    lower = text.lower()
    category_map = [
        (["spice", "masala", "haldi", "turmeric", "chilli", "mirchi", "pepper", "jeera"], "spices"),
        (["chips", "wafer", "kurkure"], "chips"),
        (["namkeen", "farsan", "bhujia", "mixture", "chiwda"], "namkeen"),
        (["dry fruit", "dryfruit", "kaju", "almond", "cashew", "raisin", "badam"], "dry fruits"),
        (["clothing", "fashion", "apparel", "garment", "textile", "fabric", "wear"], "clothing"),
        (["fmcg", "consumer goods"], "fmcg"),
        (["juice", "drink", "beverage", "sharbat", "squash", "energy drink"], "beverages"),
        (["cosmetic", "beauty", "skin", "cream", "lotion", "makeup", "lipstick"], "cosmetics"),
        (["pharma", "medicine", "ayurvedic", "herbal", "supplement", "tablet", "capsule"], "pharma"),
        (["tech", "software", "app", "digital", "startup", "it company"], "tech"),
        (["restaurant", "cafe", "dhaba", "hotel", "bakery", "sweet shop"], "restaurant"),
        (["food", "snack"], "food"),
    ]
    for keywords, category in category_map:
        if any(kw in lower for kw in keywords):
            return category
    return None


def _extract_packaging_type_from_text(text: str) -> str:
    """Extract packaging type (pouch/box/label/etc.) from text. Returns type or None."""
    if not text:
        return None
    lower = text.lower()
    if "pouch" in lower or "packet" in lower or "puch" in lower:
        return "pouch"
    if "box" in lower or "carton" in lower:
        return "box"
    if "label" in lower or "bottle label" in lower or "jar label" in lower:
        return "label"
    if "sachet" in lower or "strip" in lower:
        return "sachet"
    if "jar" in lower:
        return "jar"
    return None


def _get_recent_text(conv: dict, last_n: int = 6) -> str:
    """Get last N messages from conversation as a single text blob for keyword extraction."""
    messages = conv.get("messages", [])[-last_n:]
    return " ".join(m.get("content", "") for m in messages if isinstance(m.get("content"), str))


# ============================================================
# DASHBOARD AUTH
# ============================================================

@app.post("/auth/login")
async def login(request: Request):
    body = await request.json()
    password = body.get("password", "")

    if not verify_password(password):
        raise HTTPException(status_code=401, detail="Invalid password")

    token = create_access_token({"sub": "owner", "role": "owner"})
    return {"access_token": token, "token_type": "bearer"}


# ============================================================
# DASHBOARD API — ANALYTICS
# ============================================================

def _build_analytics() -> dict:
    conv_dir = Path("data/conversations")
    if not conv_dir.exists():
        return {
            "total_conversations": 0,
            "active_today": 0,
            "handoffs": 0,
            "stage_breakdown": {},
            "service_breakdown": {}
        }

    total = 0
    active_today = 0
    handoffs = 0
    stage_counts = {}
    service_counts = {}
    today = datetime.date.today().isoformat()

    for f in conv_dir.glob("*.json"):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                conv = json.load(fh)
        except Exception:
            continue

        total += 1
        stage = conv.get("stage", "new")
        service = conv.get("service", "unknown")
        last_updated = conv.get("last_updated", "")

        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        service_counts[service] = service_counts.get(service, 0) + 1

        if last_updated.startswith(today):
            active_today += 1
        if conv.get("handoff_triggered"):
            handoffs += 1

    return {
        "total_conversations": total,
        "active_today": active_today,
        "handoffs": handoffs,
        "stage_breakdown": stage_counts,
        "service_breakdown": service_counts
    }


@app.get("/api/analytics")
async def get_analytics(_auth=Depends(require_auth)):
    return _build_analytics()


# ============================================================
# DASHBOARD API — CONVERSATIONS
# ============================================================

@app.get("/api/conversations")
async def list_conversations(_auth=Depends(require_auth)):
    """Return all conversations as sorted list of summaries."""
    conv_dir = Path("data/conversations")
    if not conv_dir.exists():
        return []

    result = []
    for f in conv_dir.glob("*.json"):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                conv = json.load(fh)
        except Exception:
            continue

        messages = conv.get("messages", [])
        last_msg = messages[-1] if messages else {}

        result.append({
            "phone": conv.get("phone"),
            "stage": conv.get("stage", "new"),
            "service": conv.get("service", "unknown"),
            "seriousness_score": conv.get("seriousness_score", 0),
            "last_message": last_msg.get("content", "")[:120],
            "last_message_role": last_msg.get("role", ""),
            "last_updated": conv.get("last_updated", ""),
            "handoff_triggered": conv.get("handoff_triggered", False),
            "message_count": len(messages)
        })

    # Handoffs first, then by last_updated descending
    result.sort(key=lambda x: (not x["handoff_triggered"], x["last_updated"]), reverse=True)
    return result


@app.get("/api/conversations/{phone}")
async def get_conversation(phone: str, _auth=Depends(require_auth)):
    """Return full conversation including all messages."""
    conv = load_conversation(phone)
    # If conversation doesn't actually exist (newly created empty state), 404
    if not conv.get("messages") and conv.get("stage") == "new" and not conv.get("created_at"):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@app.delete("/api/conversations/{phone}")
async def reset_conversation_endpoint(phone: str, _auth=Depends(require_auth)):
    """
    Completely reset a conversation — deletes all history and stage.
    Used for testing or when owner wants to give a contact a fresh start.
    Triggered via /reset command in dashboard.
    """
    from agent.conversation import reset_conversation
    _cancel_followups(phone)
    reset_conversation(phone)

    await ws_manager.broadcast({
        "type": "conversation_reset",
        "phone": phone
    })

    return {"status": "reset", "phone": phone}


@app.delete("/api/conversations/{phone}/messages/{msg_index}")
async def delete_message(phone: str, msg_index: int, _auth=Depends(require_auth)):
    """
    Remove a specific message from conversation context by index.
    Used when AI sends a wrong message — removes it from AI memory
    so it won't reference or build on it in future replies.
    """
    from agent.conversation import save_conversation
    conv = load_conversation(phone)
    messages = conv.get("messages", [])

    if msg_index < 0 or msg_index >= len(messages):
        raise HTTPException(status_code=404, detail="Message not found")

    deleted_msg = messages[msg_index]
    del messages[msg_index]
    conv["messages"] = messages
    save_conversation(phone, conv)

    # Broadcast the deletion to dashboard clients
    await ws_manager.broadcast({
        "type": "message_deleted",
        "phone": phone,
        "msg_index": msg_index
    })

    return {"status": "deleted", "deleted_role": deleted_msg.get("role")}


@app.post("/api/conversations/{phone}/send")
async def owner_send_message(phone: str, request: Request, _auth=Depends(require_auth)):
    """
    Owner sends a message from the dashboard.
    Saves to conversation, sends via WhatsApp, broadcasts to WS clients.
    """
    body = await request.json()
    message = body.get("message", "").strip()

    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # Save to conversation with role 'owner'
    add_message(phone, "owner", message)

    # Send via WhatsApp
    result = await send_text(phone, message)

    # Broadcast to dashboard
    await ws_manager.broadcast({
        "type": "owner_message_sent",
        "phone": phone,
        "role": "owner",
        "content": message,
        "timestamp": _now_ist_iso()
    })

    return {"status": "sent", "whatsapp_result": result}


# ============================================================
# DASHBOARD WEBSOCKET
# ============================================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Real-time dashboard feed.
    Client sends JWT token as first message for authentication.
    """
    await ws_manager.connect(websocket)
    try:
        # First message must be the auth token
        token_msg = await websocket.receive_text()
        try:
            from agent.dashboard_auth import decode_token
            decode_token(token_msg)
        except Exception:
            await websocket.send_json({"type": "error", "detail": "Unauthorized"})
            await websocket.close()
            ws_manager.disconnect(websocket)
            return

        await websocket.send_json({"type": "connected", "detail": "Dashboard connected"})

        # Keep alive — client sends pings, we just read and ignore
        while True:
            await websocket.receive_text()

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception:
        ws_manager.disconnect(websocket)


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/")
async def health_check():
    return {
        "status": "SaranshDesigns AI Agent is running",
        "version": "1.0.0",
        "dashboard": "/dashboard/"
    }


@app.get("/status")
async def agent_status():
    conv_count = len(list(Path("data/conversations").glob("*.json"))) if Path("data/conversations").exists() else 0
    return {
        "status": "active",
        "conversations_tracked": conv_count,
        "owner_phone": OWNER_PHONE[-4:].rjust(len(OWNER_PHONE), "*"),
        "portfolio_path": os.getenv("PORTFOLIO_PATH", "not set")
    }


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("APP_PORT", 8000))
    print(f"🚀 SaranshDesigns AI Agent starting on port {port}")
    print(f"📊 Dashboard available at: http://localhost:{port}/dashboard/")
    is_dev = os.getenv("DEBUG", "false").lower() == "true"
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=is_dev)
