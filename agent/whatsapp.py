"""
WhatsApp API Handler
Sends messages, images, and alerts via Meta WhatsApp Business API.
"""

import os
import httpx
import base64
from pathlib import Path
from dotenv import load_dotenv
from agent.telegram_alert import send_telegram_alert

load_dotenv()

PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")
WHATSAPP_TOKEN = os.getenv("META_WHATSAPP_TOKEN")
OWNER_PHONE = os.getenv("OWNER_PHONE")

API_URL = f"https://graph.facebook.com/v22.0/{PHONE_NUMBER_ID}/messages"

HEADERS = {
    "Authorization": f"Bearer {WHATSAPP_TOKEN}",
    "Content-Type": "application/json"
}


async def send_text(to: str, message: str):
    """Send a plain text message to a WhatsApp number."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message}
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(API_URL, json=payload, headers=HEADERS)
        return response.json()


async def send_image(to: str, image_path: str, caption: str = ""):
    """Send an image from local path to a WhatsApp number."""
    # First upload the image to Meta, then send
    image_path = Path(image_path)
    if not image_path.exists():
        return {"error": "Image not found"}

    # Upload media first
    upload_url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/media"
    with open(image_path, "rb") as f:
        files = {"file": (image_path.name, f, "image/jpeg")}
        upload_headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
        async with httpx.AsyncClient() as client:
            upload_response = await client.post(
                upload_url,
                headers=upload_headers,
                files=files,
                data={"messaging_product": "whatsapp"}
            )
            media_data = upload_response.json()

    if "id" not in media_data:
        return {"error": "Failed to upload image", "details": media_data}

    media_id = media_data["id"]

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "image",
        "image": {
            "id": media_id,
            "caption": caption
        }
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(API_URL, json=payload, headers=HEADERS)
        return response.json()


async def send_portfolio_samples(to: str, image_paths: list, intro_text: str = ""):
    """Send up to 10 portfolio samples with text intro."""
    if intro_text:
        await send_text(to, intro_text)

    # Max 10 samples
    samples = image_paths[:10]
    for i, path in enumerate(samples):
        caption = f"Sample {i + 1}"
        await send_image(to, path, caption)


async def _send_template_message(to: str, template_name: str, body_params: list[str]):
    """Send a template message (bypasses 24h window restriction)."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": "en"},
            "components": [{
                "type": "body",
                "parameters": [{"type": "text", "text": p} for p in body_params]
            }]
        }
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(API_URL, json=payload, headers=HEADERS)
        return response.json()


def _build_alert_message(summary: dict) -> str:
    """Build the formatted owner alert text from a conversation summary."""
    details = summary.get("details", {})
    details_text = "\n".join([f"  • {k}: {v}" for k, v in details.items()]) if details else "  (still collecting)"

    # Multi-project summary
    projects = summary.get("projects", [])
    projects_text = ""
    if projects:
        projects_text = "\n\n📦 Multiple Projects:"
        for p in projects:
            p_details = "\n".join([f"    - {k}: {v}" for k, v in p.get("details", {}).items()]) or "    (details pending)"
            projects_text += f"\n  Project {p['id']} — {p['service'].upper()}:\n{p_details}\n  Agreed: {'₹' + str(p['agreed_price']) if p.get('agreed_price') else 'TBD'}"

    # Existing logo flag
    existing_logos = [img for img in summary.get("images_received", []) if img.get("tag") == "existing_logo"]
    logo_note = "\n⚠️ Logo Redesign: Client has an existing logo — this is an improvement, not fresh design." if existing_logos else ""

    return f"""🔔 SaranshDesigns Agent Alert

📋 Service: {summary.get('service', 'Unknown').upper()}
📱 Client: {summary.get('phone', 'Unknown')}
🎯 Stage: {summary.get('stage', 'Unknown').replace('_', ' ').title()}
📊 Seriousness Score: {summary.get('seriousness_score', 0)}/100

📝 Details Collected:
{details_text}

💰 Agreed Price: {'₹' + str(summary.get('agreed_price')) if summary.get('agreed_price') else 'Not yet confirmed'}
🖼️ Images Received: {summary.get('images_count', 0)}{logo_note}{projects_text}

📌 Notes:
{chr(10).join(['  • ' + n for n in summary.get('notes', [])]) if summary.get('notes') else '  None'}

⚡ Action Required: Please message the client to proceed with advance payment and project initiation."""


async def send_owner_alert(summary: dict):
    """
    Send an alert to the Owner when a sale is closing or escalation needed.
    Primary: template message (bypasses 24h window).
    Fallback: plain text (works only within 24h window).
    """
    alert_message = _build_alert_message(summary)

    if not OWNER_PHONE:
        print("⚠️ OWNER_PHONE not set — alert not sent!")
        return alert_message

    # --- Primary: Template message (no 24h restriction) ---
    print(f"📣 Sending owner alert via TEMPLATE to {OWNER_PHONE}...")
    template_result = await _send_template_message(
        OWNER_PHONE, "owner_alert_handoff", [alert_message]
    )
    print(f"📣 Template API response: {template_result}")

    # --- Parallel: Telegram alert (backup channel, always attempted) ---
    try:
        tg_ok = await send_telegram_alert(alert_message)
        print(f"📨 Telegram alert: {'sent ✅' if tg_ok else 'skipped/failed'}")
    except Exception as e:
        print(f"📨 Telegram alert error (non-fatal): {e}")

    # Check if template succeeded
    if template_result.get("messages"):
        print("✅ Owner alert sent via template successfully.")
        return alert_message

    # --- Fallback: Plain text (only works within 24h window) ---
    print(f"⚠️ Template failed — falling back to plain text for {OWNER_PHONE}...")
    fallback_result = await send_text(OWNER_PHONE, alert_message)
    print(f"📣 Fallback plain text API response: {fallback_result}")

    return alert_message


async def send_escalation_alert(phone: str, client_question: str, service: str, budget: str = "N/A", timeline: str = "N/A"):
    """Send escalation alert to Owner when agent can't handle something."""
    message = f"""⚠️ *Owner Escalation Required*

*Owner Alert:*
Client asking: {client_question}
Budget: {budget}
Timeline: {timeline}
Service: {service}
Client Phone: {phone}

What should I reply?"""

    if OWNER_PHONE:
        print(f"⚠️ Sending escalation alert to {OWNER_PHONE}...")
        result = await send_text(OWNER_PHONE, message)
        print(f"⚠️ Escalation alert API response: {result}")

    # Parallel: Telegram
    try:
        tg_ok = await send_telegram_alert(message)
        print(f"📨 Telegram escalation: {'sent ✅' if tg_ok else 'skipped/failed'}")
    except Exception as e:
        print(f"📨 Telegram escalation error (non-fatal): {e}")


async def download_media(media_id: str) -> bytes:
    """Download media (image) sent by client."""
    # Step 1: Get media URL
    url = f"https://graph.facebook.com/v19.0/{media_id}"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        media_info = response.json()

    if "url" not in media_info:
        return None

    # Step 2: Download the actual file
    async with httpx.AsyncClient() as client:
        response = await client.get(media_info["url"], headers=headers)
        return response.content


def encode_image_to_base64(image_bytes: bytes) -> str:
    """Convert image bytes to base64 string for OpenAI vision."""
    return base64.b64encode(image_bytes).decode("utf-8")


async def mark_message_read(wamid: str):
    """Mark a WhatsApp message as read."""
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": wamid
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(API_URL, json=payload, headers=HEADERS)
        return response.json()


async def send_reaction(to: str, wamid: str, emoji: str):
    """Send an emoji reaction to a specific WhatsApp message."""
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "reaction",
        "reaction": {
            "message_id": wamid,
            "emoji": emoji
        }
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(API_URL, json=payload, headers=HEADERS)
        return response.json()


async def _upload_media(file_path: str, mime_type: str) -> str:
    """Upload a media file to Meta and return its media_id."""
    upload_url = f"https://graph.facebook.com/v22.0/{PHONE_NUMBER_ID}/media"
    upload_headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    p = Path(file_path)
    with open(p, "rb") as f:
        files = {"file": (p.name, f, mime_type)}
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                upload_url,
                headers=upload_headers,
                files=files,
                data={"messaging_product": "whatsapp"}
            )
            data = resp.json()
    if "id" not in data:
        raise ValueError(f"Media upload failed: {data}")
    return data["id"]


async def send_document(to: str, file_path: str, filename: str, caption: str = ""):
    """Send a document file to a WhatsApp number."""
    media_id = await _upload_media(file_path, "application/octet-stream")
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "document",
        "document": {"id": media_id, "filename": filename, "caption": caption}
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(API_URL, json=payload, headers=HEADERS)
        return response.json()


async def send_video(to: str, video_path: str, caption: str = ""):
    """Send a video file to a WhatsApp number."""
    media_id = await _upload_media(video_path, "video/mp4")
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "video",
        "video": {"id": media_id, "caption": caption}
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(API_URL, json=payload, headers=HEADERS)
        return response.json()


async def send_audio(to: str, audio_path: str):
    """Send an audio file to a WhatsApp number."""
    media_id = await _upload_media(audio_path, "audio/mpeg")
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "audio",
        "audio": {"id": media_id}
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(API_URL, json=payload, headers=HEADERS)
        return response.json()


async def send_location(to: str, lat: float, lon: float, name: str = "", address: str = ""):
    """Send a location pin to a WhatsApp number."""
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "location",
        "location": {
            "latitude": lat,
            "longitude": lon,
            "name": name,
            "address": address
        }
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(API_URL, json=payload, headers=HEADERS)
        return response.json()


async def send_interactive_buttons(to: str, body_text: str, buttons: list):
    """Send an interactive button message (max 3 buttons) to a WhatsApp number."""
    btn_list = [
        {"type": "reply", "reply": {"id": b["id"], "title": b["title"][:20]}}
        for b in buttons[:3]
    ]
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body_text},
            "action": {"buttons": btn_list}
        }
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(API_URL, json=payload, headers=HEADERS)
        return response.json()
