"""
Output filter for WhatsApp bot replies.

Scans outgoing messages for leaked secrets, phone numbers, emails,
UPI IDs, internal paths, and env-var values before they reach the user.
"""

from __future__ import annotations

import re
from modules.logging_config import get_logger, hash_phone
from agent.telegram_alert import send_telegram_alert

log = get_logger()

# ---------------------------------------------------------------------------
# Generic safe reply (Hindi-English mix matching the bot's tone)
# ---------------------------------------------------------------------------
GENERIC_REPLY = (
    "मुझे इसकी exact जानकारी नहीं है, Saransh sir से confirm कर लूं? \U0001f64f"
)

# ---------------------------------------------------------------------------
# Allowed-lists
# ---------------------------------------------------------------------------
ALLOWED_EMAILS: set[str] = {
    "radharamangd@gmail.com",
    "saransh@saransh.space",
}
ALLOWED_EMAIL_DOMAIN = "@saranshdesigns.com"

# URLs that look like paths but are actually portfolio links — never block
SAFE_URL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"https?://[^\s]+", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Leak-detection patterns
# ---------------------------------------------------------------------------

# 1. API keys / tokens
_RE_OPENAI_KEY = re.compile(r"sk-[A-Za-z0-9_-]{20,}")
_RE_BEARER = re.compile(r"Bearer\s+[A-Za-z0-9_\-.]{20,}")
_RE_SLACK_TOKEN = re.compile(r"xox[bp]-[A-Za-z0-9-]{10,}")
_RE_LONG_SECRET = re.compile(
    r"""(?:['"]|(?:key|token|secret|password|api_key|apikey)\s*[:=]\s*['"]?)"""
    r"""([A-Za-z0-9_\-/+=]{40,})""",
    re.IGNORECASE,
)

# 2. Indian phone numbers (10 digits starting 6-9, optional +91/91 prefix)
_RE_INDIAN_PHONE = re.compile(
    r"(?<!\d)(?:\+?91[\s-]?)?([6-9]\d{9})(?!\d)"
)

# 3. Email addresses
_RE_EMAIL = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)

# 4. UPI IDs (name@upi, name@paytm, name@ybl, name@oksbi, etc.)
_RE_UPI = re.compile(
    r"[A-Za-z0-9._-]+@(?:upi|paytm|ybl|oksbi|okaxis|okicici|okhdfcbank|apl|axl|ibl|sbi|axisbank|icici|hdfcbank|kotak|indus|federal|rbl|yesbank|bob|pnb|barodampay|jupiteraxis)\b",
    re.IGNORECASE,
)

# 5. Internal system paths
_RE_SYSTEM_PATH = re.compile(
    r"(?:^|[\s\"'`(])(/(?:opt|home|etc|var|tmp)/[^\s\"'`)<>]{2,}|[A-Za-z]:\\\\[^\s\"'`)<>]{2,})",
    re.MULTILINE,
)

# 6. Environment variable values (KEY=value patterns from .env)
_RE_ENV_VAR = re.compile(
    r"(?:DATABASE_URL|OPENAI_API_KEY|API_KEY|SECRET_KEY|META_APP_SECRET|"
    r"WHATSAPP_TOKEN|TELEGRAM_BOT_TOKEN|SENTRY_DSN|REDIS_URL|"
    r"POSTGRES_PASSWORD|JWT_SECRET|SESSION_SECRET|AWS_SECRET_ACCESS_KEY|"
    r"STRIPE_SECRET_KEY|WEBHOOK_VERIFY_TOKEN)\s*=\s*\S+",
    re.IGNORECASE,
)


def _strip_urls(text: str) -> str:
    """Return text with URLs removed so path regex doesn't match them."""
    return re.sub(r"https?://[^\s]+", "", text)


def _normalise_phone(raw: str) -> str:
    """Strip +91 / 91 prefix and whitespace to get bare 10-digit number."""
    digits = re.sub(r"[^\d]", "", raw)
    if len(digits) == 12 and digits.startswith("91"):
        return digits[2:]
    return digits[-10:] if len(digits) >= 10 else digits


def _check_api_keys(text: str) -> str | None:
    for pat, label in [
        (_RE_OPENAI_KEY, "openai_key"),
        (_RE_BEARER, "bearer_token"),
        (_RE_SLACK_TOKEN, "slack_token"),
        (_RE_LONG_SECRET, "long_secret"),
    ]:
        if pat.search(text):
            return label
    return None


def _check_phone_numbers(text: str, current_phone: str) -> str | None:
    current_normalised = _normalise_phone(current_phone)
    for match in _RE_INDIAN_PHONE.finditer(text):
        found = _normalise_phone(match.group(0))
        if found != current_normalised:
            return "phone_number"
    return None


def _check_emails(text: str) -> str | None:
    for match in _RE_EMAIL.finditer(text):
        email = match.group(0).lower()
        if email in ALLOWED_EMAILS:
            continue
        if email.endswith(ALLOWED_EMAIL_DOMAIN):
            continue
        return "email_address"
    return None


def _check_upi(text: str) -> str | None:
    if _RE_UPI.search(text):
        return "upi_id"
    return None


def _check_system_paths(text: str) -> str | None:
    # Strip URLs first so portfolio links aren't flagged
    cleaned = _strip_urls(text)
    if _RE_SYSTEM_PATH.search(cleaned):
        return "system_path"
    return None


def _check_env_vars(text: str) -> str | None:
    if _RE_ENV_VAR.search(text):
        return "env_variable"
    return None


# Ordered checkers — stop at first match
_CHECKERS: list[tuple[str, ...]] = []  # populated below


async def filter_output(
    text: str, current_phone: str
) -> tuple[str, bool]:
    """
    Scan an outgoing bot reply for information leaks.

    Returns
    -------
    (filtered_text, was_blocked)
        If a leak is detected:  (GENERIC_REPLY, True)
        If clean:               (original text, False)
    """
    leak_type: str | None = None

    # Run each checker; short-circuit on first hit
    leak_type = _check_api_keys(text)
    if leak_type is None:
        leak_type = _check_env_vars(text)
    if leak_type is None:
        leak_type = _check_phone_numbers(text, current_phone)
    if leak_type is None:
        leak_type = _check_emails(text)
    if leak_type is None:
        leak_type = _check_upi(text)
    if leak_type is None:
        leak_type = _check_system_paths(text)

    if leak_type is None:
        return (text, False)

    # --- Leak detected ---
    preview = text[:100]
    phone_hash = hash_phone(current_phone)

    log.warning(
        "output_filter.leak_blocked",
        severity="high",
        leak_type=leak_type,
        reply_preview=preview,
        phone_hash=phone_hash,
    )

    try:
        await send_telegram_alert(
            f"\U0001f6a8 OUTPUT LEAK BLOCKED\n"
            f"Type: {leak_type}\n"
            f"Phone: {phone_hash}\n"
            f"Preview: {preview}"
        )
    except Exception:
        log.error(
            "output_filter.telegram_alert_failed",
            leak_type=leak_type,
            exc_info=True,
        )

    return (GENERIC_REPLY, True)
