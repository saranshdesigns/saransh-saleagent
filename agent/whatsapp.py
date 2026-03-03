"""
WhatsApp API Handler
Sends messages, images, and alerts via Meta WhatsApp Business API.
"""

import os
import httpx
import base64
from pathlib import Path
from dotenv import load_dotenv

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


async def send_owner_alert(summary: dict):
    """Send an alert to the Owner when a sale is closing or escalation needed."""
    details = summary.get("details", {})
    details_text = "\n".join([f"  • {k}: {v}" for k, v in details.items()]) if details else "  (still collecting)"

    alert_message = f"""🔔 *SaranshDesigns Agent Alert*

📋 *Service:* {summary.get('service', 'Unknown').upper()}
📱 *Client:* {summary.get('phone', 'Unknown')}
🎯 *Stage:* {summary.get('stage', 'Unknown').replace('_', ' ').title()}
📊 *Seriousness Score:* {summary.get('seriousness_score', 0)}/100

📝 *Details Collected:*
{details_text}

💰 *Agreed Price:* {'₹' + str(summary.get('agreed_price')) if summary.get('agreed_price') else 'Not yet confirmed'}
🖼️ *Images Received:* {summary.get('images_count', 0)}

📌 *Notes:*
{chr(10).join(['  • ' + n for n in summary.get('notes', [])]) if summary.get('notes') else '  None'}

⚡ *Action Required:* Please message the client to proceed with advance payment and project initiation."""

    if OWNER_PHONE:
        await send_text(OWNER_PHONE, alert_message)
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
        await send_text(OWNER_PHONE, message)


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
