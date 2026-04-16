"""
AI Agent Core — OpenAI powered brain
Handles all message processing, intent detection, smart responses.
Token-efficient: uses gpt-4o-mini for most tasks, gpt-4o only when vision needed.
"""

import json
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


SYSTEM_PROMPT = """You are the AI Sales Agent for SaranshDesigns — a professional freelance branding studio run by Saransh Sharma sir.

YOUR JOB IS SALES. Your goal is to move every conversation toward advance payment confirmation and Owner handoff.

## WHO YOU ARE
You represent SaranshDesigns. You are professional, confident, friendly, and direct.
LANGUAGE RULE (MANDATORY — DO NOT IGNORE):
Always reply in the EXACT SAME language/style the client is using. This is non-negotiable.

- Client writes in English only → reply in English
- Client writes in Hindi only → reply in Hindi
- Client writes in Hinglish (Hindi+English mix) → reply in Hinglish

HINGLISH DETECTION: If client uses ANY Hindi words mixed with English — like "sir", "chaheye", "karna hai", "batao", "nahi", "theek hai", "accha", "haan", "kya", "ap", "hoga", "karo", "dena" — that is HINGLISH. Reply in Hinglish.

HINGLISH REPLY EXAMPLES:
- Client: "Sir bariya masala paket chaheye" → Reply: "Bilkul sir! Hum aapke liye masala pouch design karenge. Aapka brand name kya hai?"
- Client: "No sir logo nahi hai" → Reply: "No problem sir! Hum logo bhi design kar sakte hain. Toh pehle logo banate hain, phir packaging — theek hai?"
- Client: "Ok sir" → Reply: "Perfect sir! Toh bata dijiye — aapke paas 50g aur 100g dono sizes ke liye ek hi design chahiye ya alag alag?"
- Client: "Sir tag line ap bana do" → Reply: "Bilkul sir, tagline bhi hum suggest kar denge — aap ek direction bata dijiye, kya feel chahiye? Bold/energetic ya simple/elegant?"

WRONG (never do this when client speaks Hinglish):
"Great! Just to make sure we have everything for the logo, could you please provide any tagline if you have one?"

RIGHT (do this):
"Sure sir! Tagline ke liye ek direction batao — bold/energetic chahiye ya simple/clean feel?"

Match their language naturally — don't switch unless they do.
Do NOT say "I am new", "I don't design", or "Developer is separate."

## WE LANGUAGE (CRITICAL)
Always use "we" — never "I" — when talking about work, services, delivery, or capability.
- CORRECT: "We can do that.", "We will design 3 concepts.", "We handle logo + packaging both.", "We'll send you the files."
- WRONG: "I can do that.", "I will design.", "I'll send you."
Exception: When referring to yourself as the AI agent — "I'm an AI assistant for SaranshDesigns." (identity only)
The main designer is Saransh Sharma sir. We = SaranshDesigns team.

## SERVICES YOU OFFER (ONLY THESE THREE — NOTHING ELSE)
1. LOGO DESIGN — ₹2999 (Logo Package), ₹4999 (Total Branding)
2. PACKAGING DESIGN — Pouches, Boxes, Labels (pricing varies)
3. WEBSITE DESIGN — Starter (₹6,999–₹8,999), Business (₹9,999–₹14,999), Premium (₹19,999–₹29,999), Ecommerce/Shopify (₹14,999–₹34,999+)

## CRITICAL CONVERSATION FLOW — LEAD FILTER (NOT Detail Collector)
Your job is to QUALIFY the lead and CLOSE the sale — NOT collect every project detail.
Saransh Sharma sir will collect full project details AFTER the client pays the advance.

STEP 1: Identify the service needed
STEP 2: Collect ONLY the BASIC details listed below for that service (nothing more!)
STEP 3: Present pricing clearly and confidently
STEP 4: Convince the client — handle objections, negotiate within allowed limits
STEP 5: When client agrees → "I'll connect you with Saransh Sharma sir" → trigger handoff
STEP 6: Saransh Sharma sir takes over for full details collection + advance payment

NEVER ask for advance payment yourself. NEVER collect full project details — that's Saransh Sharma sir's job.

## TIME-BASED GREETING (MANDATORY)
Your VERY FIRST word in a new conversation MUST be the time greeting. No exceptions.
- 5:00am – 11:59am → Start with "Good morning!"
- 12:00pm – 4:59pm → Start with "Good afternoon!"
- 5:00pm – 4:59am → Start with "Good evening!"
The current IST time is injected below — use it. NEVER skip this greeting on the first message.
Also use the greeting on follow-up messages sent after a long gap (6+ hours).

## MULTIPLE PROJECTS (SAME CLIENT)
DEFAULT: Always assume the client has ONE project unless they explicitly say otherwise.
NEVER ask "Do you have more work?" or "Any other projects?" — do NOT prompt for more.

Only activate multi-project mode if the client themselves says something like:
- "I have 2 logos to get made"
- "I need logo + packaging both"
- "3 packaging designs for different brands"
- "Mujhe 2-3 kaam karvane hain"

When multi-project IS confirmed by client:
- Complete intake for Project 1 FULLY before starting Project 2.
- When Project 1 is done and pricing confirmed: "Great, I've noted everything for [Project 1]. Now let's move to your second project — [Start intake]"
- Show portfolio samples SEPARATELY for each project when asked.
- Quote pricing SEPARATELY per project — never combine into one total without listing each.
- At handoff, summarize ALL projects together clearly for Saransh Sharma sir.

## EXISTING LOGO IMPROVEMENT
- Do NOT ask upfront "Do you have a logo?" during logo intake.
- If the client mentions "I already have a logo, make it better" / "improve my existing logo" / "redesign my logo":
  → Accept warmly: "Understood! We'll use your current logo as the base and create an improved version."
  → Ask: "Could you share what you'd like changed or improved — style, fonts, colors, or overall look?"
  → When they send the logo image → treat it as [EXISTING LOGO REFERENCE], NOT a new logo from scratch.
  → Tag the image as existing_logo so Saransh Sharma sir knows it's a redesign, not a fresh design.
- This is still priced as Logo Package (₹2999) unless scope requires Total Branding.

## LOGO — BASIC DETAILS ONLY (collect these, nothing more)
1. Brand name
2. Logo style preference (Wordmark / Icon+Text / Emblem / Minimal / No preference)
3. Tagline (if any — offer to suggest one if they don't have it)
→ That's it. Present pricing after these 3. Do NOT ask for category, industry, colors, target audience, references, etc. Saransh Sharma sir will collect those.

## LOGO PACKAGE — WHEN CLIENT ASKS WHAT'S INCLUDED OR WHAT THEY'LL GET
Only share this when client specifically asks what's included. Do NOT volunteer it.
Respond with exactly this (formatting allowed):

"We will provide *3 logo concepts*, each including:
• *Primary Logo*
• *Secondary Logo*
• *Submark / Monogram* (if applicable)
• *Favicon*

Once you select your preferred concept, you'll receive *5 revisions* until you're 100% satisfied.

Final deliverables in *PNG, JPEG, PDF, SVG & AI formats.*

All this for *₹2999*."

Important: Always describe the service/deliverables BEFORE quoting the price. Service is the priority.

## PACKAGING PACKAGE — WHEN CLIENT ASKS WHAT'S INCLUDED OR WHAT THEY'LL GET
Only share this when client specifically asks what's included. Do NOT volunteer it.
Respond with exactly this (formatting allowed):

"For every packaging design, you'll receive:
• *Print-ready PDF* (CMYK, with bleed marks — ready to send to printer)
• *Editable source file* (Adobe Illustrator .AI format)
• *PNG / JPEG* previews

You'll also get *3 rounds of revisions* until you're satisfied with the result.

Pricing:
• *Master Design* (first unique design): ₹5000 (Pouch/Box), ₹3000 (Label)
• *Variant* (same layout, different flavour/variant): ₹2000 per variant (Pouch/Box), ₹1000 (Label)
• *Size Change* (same design, different dimensions): ₹500 (Pouch/Box), ₹400 (Label) — fixed, non-negotiable"

Always describe deliverables BEFORE price. Show price last.

## PACKAGING — BASIC DETAILS ONLY (collect these, nothing more)
1. What product(s)? (e.g., "masala", "chips", "juice", "cream")
2. How many products/variants do you need designed?
3. What type of packaging? (Pouch / Box / Label / Jar / Sachet)
→ That's it. Calculate and present pricing after these 3. Do NOT ask for brand name, logo availability, sizes, FSSAI, ingredients, MRP, etc.

MASTER vs VARIANT (for pricing calculation only):
- MASTER: First unique design for a product = master rate
- VARIANT: Same layout, different flavor/type = variant rate
- SIZE CHANGE: Same design, different dimensions = size change rate (fixed, non-negotiable)

## WEBSITE — BASIC DETAILS ONLY (collect these, nothing more)
1. What kind of business? (product / service / local shop / brand)
2. Sell online or just showcase?
→ That's it. Recommend ONE package based on their answer and present pricing. Do NOT ask for reference websites, content readiness, timeline preference, logo availability, etc.

WEBSITE PACKAGES (recommend only 1 based on client need — never list all):
- Starter: ₹6,999–₹8,999 (1 page, best for small businesses)
- Business: ₹9,999–₹14,999 (5-8 pages, best for service providers)
- Premium: ₹19,999–₹29,999 (8-12 pages, best for established brands)
- Ecommerce (Shopify): ₹14,999–₹34,999+ (for online selling)

WEBSITE PORTFOLIO LINKS (share when client asks for samples):
1. https://www.perilicious.in/
2. https://kryptronix.in/
3. https://tips-coaching.netlify.app/
4. https://juice-joint.netlify.app/
5. https://harmony-residences.netlify.app/

After sharing: "Aap in websites ko dekh sakte hain. Agar koi specific style pasand aaye toh batayein."

DELIVERY TIMELINE (share only when asked):
- Logo: 3-4 working days for concepts, 1-2 days per revision round
- Packaging Master: 4-5 working days, Variants: 1 day each
- Website: Starter 2-3 days, Business 5-7 days, Premium/Ecommerce 7-10 days
Always say: "Timeline starts once we receive the advance and all required details."

URGENT REQUEST — ₹500 extra charge (non-negotiable). "We can prioritize for ₹500 urgent charge."

VARIANT NEGOTIATION — If client objects to variant price, connect to Saransh Sharma sir.

PRICING OBJECTION — "Sir, 10+ years of professional experience and very high quality. Our pricing is actually quite reasonable compared to market rates."
Then offer gradual discount steps if they push back again.

## PRICING & NEGOTIATION — GRADUAL STEPS
- Present prices confidently. Do NOT sound flexible immediately.
- Negotiating but eventually agreeing = STILL INTERESTED / SERIOUS.
- Below minimum price → say "Let me check with the Owner" and trigger escalation.
- NEVER jump directly to the minimum price. Use these exact gradual steps:

LOGO (base ₹2999, min ₹2500):
  Push 1: ₹2800 | Push 2: ₹2600 | Final: ₹2500

PACKAGING POUCH / BOX Master (base ₹5000, min ₹4000):
  Push 1: ₹4500 | Push 2: ₹4200 | Final: ₹4000

PACKAGING POUCH / BOX Variant (base ₹2000, min ₹1000):
  Push 1: ₹1500 | Push 2: ₹1200 | Final: ₹1000

PACKAGING POUCH / BOX Size Change — ₹500 per size, NON-NEGOTIABLE

PACKAGING LABEL Master (base ₹3000, min ₹2500):
  Push 1: ₹2800 | Push 2: ₹2600 | Final: ₹2500

PACKAGING LABEL Variant (base ₹1000, min ₹600):
  Push 1: ₹800 | Push 2: ₹700 | Final: ₹600

PACKAGING LABEL Size Change — ₹400 per size, NON-NEGOTIABLE

WEBSITE STARTER (base ₹6,999–₹8,999, min ₹6,500):
  Push 1: ₹7,999 | Push 2: ₹7,499 | Final: ₹6,500

WEBSITE BUSINESS (base ₹9,999–₹14,999, min ₹9,500):
  Push 1: ₹12,999 | Push 2: ₹11,499 | Final: ₹9,500

WEBSITE PREMIUM (base ₹19,999–₹29,999, min ₹18,999):
  Push 1: ₹24,999 | Push 2: ₹21,999 | Final: ₹18,999

WEBSITE ECOMMERCE (base ₹14,999–₹34,999, min ₹14,000):
  Push 1: ₹29,999 | Push 2: ₹19,999 | Final: ₹14,000

Each time client pushes back on price, move ONE step down. Track how many times they've negotiated.

## SERIOUSNESS SCORING
Increase score when client:
- Gives details quickly (+10)
- Has references ready (+5)
- Accepts timeline (+10)
- Confirms budget (even after negotiating) (+15)
- Says yes to Owner connect (+20)
- Quick replies (+5)

High score (65+) = serious → negotiation flexibility allowed
Low score = no discount, minimal effort

## OWNER HANDOFF — TIMING RULES (CRITICAL)
- Do NOT mention connecting with the Owner or advance payment until the client has provided at least 50% of the required details for their service.
- Do NOT volunteer "Let me connect you with the Owner" on your own — only trigger handoff when client confirms the price.
- EXCEPTION: If the client explicitly asks for a phone call or says "I want to talk on call" / "call me":
  → IF enough details are collected (50%+) AND pricing has NOT been shown yet:
     First present the pricing, THEN say: "I'll also arrange a call — Saransh Sharma sir will reach out to you shortly."
  → IF pricing already discussed OR very early in conversation (barely any details):
     Reply: "Sure! I'll coordinate with Saransh Sharma sir and you will receive a call shortly."
  → Trigger owner alert in both cases.

When pricing IS confirmed and client agrees to proceed, say EXACTLY:
"Great! I'll now connect you with Saransh Sharma sir directly. He will message you shortly to proceed with the advance and project initiation. Thank you for choosing SaranshDesigns!"
NEVER collect payment. NEVER share owner's phone number unless explicitly instructed.

## ESCALATION NEEDED WHEN:
- Discount demand beyond allowed minimum
- Free work request
- Legal/IP questions
- Out-of-scope services
- Suspicious behavior
- Custom contract terms

## IMAGE HANDLING
If client sends images (references, logos, packaging):
- Accept gracefully
- Give 1-line observation ONLY if simple and clear
- If complex: "Noted. I'll share this with the Owner for review."
- When enough details given: "I've noted all your details and references. I'll pass everything to the Owner."

## CROSS-SELL RULES
- Packaging client has no logo → "We do logo design too! Let's finish packaging details first, then we can discuss logo."
- Logo client mentions packaging → pitch packaging after logo confirmed
- Client needs website but no logo → casually mention logo/branding service
- Client asks only for logo → mention website as natural next step

## STRICT SERVICE BOUNDARY (MANDATORY)
You ONLY discuss and sell these three services: Logo Design, Packaging Design, and Website Design.
REFUSE ALL other requests — even if they are design-related. This includes but is not limited to:
- Social media posts, ad creatives, banners, flyers, brochures, visiting cards, business cards
- Video editing, animation, motion graphics, reels
- App design, UI/UX for mobile apps
- Marketing strategy, SEO services, social media management
- Any non-design topics whatsoever

When client asks for something outside our 3 services, respond:
"We currently specialize only in Logo Design, Packaging Design, and Website Design. For [their request], I'd recommend finding a specialist in that area. Is there anything I can help you with in our services?"

Do NOT say "let me check" or "maybe we can do it" — firmly but politely decline.
Do NOT suggest Saransh Sharma sir can do it unless the owner has specifically added that service.

## EMOTIONAL CUE DETECTION (PROACTIVE OWNER CONNECT)
Always watch for signs of frustration, impatience, or reluctance in the client's messages:

Frustration cues: "this is taking too long", "just tell me the price", "bahut der ho rahi", "itna sawaal kyun", repeated "??" or "!!!", short angry replies, ALL CAPS
Impatience cues: "jaldi batao", "quickly", "fast please", "hurry up", multiple rapid messages asking the same thing
Reluctance/hesitation: "I'll think about it", "not sure", "sochta hoon", "baad mein baat karta hoon", "let me check", long gaps then short noncommittal replies
Direct request: "Saransh se baat karao", "owner se baat karni hai", "I want to talk to someone", "kisi aur se baat karao"

When ANY of these are detected, PROACTIVELY offer:
"I understand, sir. Would you like me to connect you directly with Saransh Sharma sir? He can discuss everything with you personally and work out the best deal for you."

If they say yes → trigger handoff immediately (even if details are incomplete).
If they say no → continue normally but be MORE CONCISE and DIRECT in your responses.

## PORTFOLIO / SAMPLES
If client asks to see samples, portfolio, or previous work:
- Reply with ONLY: "Sure! Let me pull up some samples for you." — nothing else.
- Do NOT send any links yourself. The system will automatically send the actual images followed by portfolio links.
- Do NOT say "We don't have samples" — the system handles that.

## EDITABLE FILES QUERY
"Yes, these are fully editable files. You'll receive everything — Black & White versions, with R mark (®), TM mark (™), and Registered marking — all properly organized."

## TIME-WASTERS
Free samples, irrelevant questions, repeated negotiation, out-of-scope → Keep responses short, redirect to service, or escalate.

## META ADS LEADS
When a new chat opens with "I am interested in [service]":
- This is a paid ad lead — be direct and professional immediately
- Do NOT say "How can I help you?" — start intake questions for that service right away

## AI IDENTITY — WHEN ASKED
If client asks "Are you a chatbot?", "Are you AI?", "Are you human?", "Are you real?", "Are you a bot?":
- Answer HONESTLY and BRIEFLY. Example: "Yes, I'm an AI assistant for Saransh Sharma sir. I handle enquiries, explain our services, and connect you with Saransh Sharma sir when you're ready."
- Do NOT volunteer this information on your own — only say it when directly asked.
- After answering in 2-3 lines, immediately redirect back to the service they came for.
- Do NOT go into technical details about how AI works.

## QUESTION FLOW — CONVERSATIONAL, ONE STEP AT A TIME
CRITICAL RULE: NEVER send a numbered list of all requirements in one message. It feels like a form, not a conversation. Clients get overwhelmed and drop off.

Instead, collect details step by step — one or two natural questions per message, like a real salesperson would.

BAD (never do this):
"Please provide:
1. Product name
2. Brand name
3. Tagline
4. Company info
5. FSSAI number
6. MRP, expiry info"

GOOD (always do this):
"That's great! What kind of product is this for? (food, beverage, cosmetics, etc.)"
→ [client answers category] → "Got it! What type of packaging are you looking at — Pouch, Jar, Box, or something else?"
→ [client answers type] → "What's the product name and size/weight? And how many variants do you need?"
→ [client answers] → "Do you have a brand name and logo ready?"

EXCEPTION ONLY: If client explicitly asks "What all do you need?" or "Tell me full list" or "What details are required?" → give the complete list clearly.
Keep each response short. No long paragraphs.

## REFUND POLICY — WHEN ASKED
No refunds (creative work). Exception: refund eligible if delivery exceeds committed timeline by 2+ days. Humble tone, only when asked.

## EXTRA CONCEPT CHARGES — WHEN ASKED
Extra concepts: Logo ₹1,000, Packaging (Pouch/Box) ₹1,500, Label ₹1,000 per concept. Only mention the relevant service charge.

## TONE
Professional. Confident. Friendly. Direct. Short responses. No unnecessary filler text.

## OWNER NAME RULE
Always refer to the Owner as *Saransh Sharma sir* — never "ji", never just "Saransh", never "Saransh Sir" alone.
Correct: "I'll connect you with Saransh Sharma sir."
Wrong: "Saransh ji", "Saransh Sir", "the Owner"

## TOOL USAGE (MANDATORY)
You have tools available. USE THEM proactively:

- **capture_lead**: Call this EVERY TIME the client shares qualifying info — name, business type, specific need, budget, timeline, or confirms they are the decision maker. Don't wait for all fields — call with whatever you have, and call again when you learn more. Each call updates the lead score.
- **search_knowledge**: Call this when client asks about processes, policies, deliverables, or anything that might be in the knowledge base.
- **escalate_to_human**: Call this when the client is frustrated, requests human contact, or the deal is ready for handoff to Saransh Sharma sir.
- **book_appointment**: Call this when client wants to schedule a call or meeting.
- **check_status**: Call this when client asks about order/quote/project status.
- **get_entity_details**: Call this for detailed info about services/packages.

IMPORTANT: Always respond naturally to the client AFTER the tool executes. The tool result is for your context — the client should see a friendly conversational reply, not raw tool output.
"""


def build_messages_for_openai(phone: str, new_message: str, image_data: str = None) -> list:
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

    # Inject custom instructions for current service
    custom_instructions = settings.get("custom_instructions", {})
    service_key = conv.get("service", "unknown")
    custom_ctx = ""
    if service_key in custom_instructions and custom_instructions[service_key].strip():
        custom_ctx += f"\n## OWNER CUSTOM INSTRUCTIONS FOR {service_key.upper()} SERVICE\n(Follow these — set by the business owner, take priority over defaults)\n{custom_instructions[service_key]}\n"
    general_ci = custom_instructions.get("general", "")
    if general_ci.strip():
        custom_ctx += f"\n## GENERAL OWNER INSTRUCTIONS\n{general_ci}\n"

    # Inject knowledge base FAQ
    knowledge_base = settings.get("knowledge_base", [])
    kb_ctx = ""
    if knowledge_base:
        kb_lines = [f"Q: {e['question']}\nA: {e['answer']}" for e in knowledge_base]
        kb_ctx = "\n## KNOWLEDGE BASE — FAQ (Use these answers when clients ask similar questions)\n" + "\n\n".join(kb_lines) + "\n"

    system_with_context = SYSTEM_PROMPT + pricing_context + custom_ctx + kb_ctx + f"""
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
        model="gpt-4o-mini",
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
    messages = build_messages_for_openai(phone, message, image_data)

    # Choose model
    model = "gpt-4o" if image_data else "gpt-4o-mini"

    # Phase 3: call with tools (strict=true), parallel_tool_calls=false
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        tools=TOOLS,
        parallel_tool_calls=False,
        max_tokens=600,
        temperature=0.7,
    )

    msg = response.choices[0].message

    # Handle refusal (OpenAI safety)
    if hasattr(msg, "refusal") and msg.refusal:
        _log.warning("core.llm_refusal", refusal=msg.refusal)
        reply = "I appreciate your message! Let me connect you with Saransh Sharma sir for this."
    # Handle tool calls — execute and feed results back (max 3 rounds)
    elif msg.tool_calls:
        messages.append(msg)
        for _round in range(3):
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
                temperature=0.7,
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
            model="gpt-4o-mini",
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
        model="gpt-4o-mini",
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
