"""
Input sanitization and filtering for WhatsApp messages.

Strips prompt-injection patterns before messages reach the LLM,
while preserving normal Hindi / English / Hinglish text.
Flagged messages are logged but never blocked (monitoring-only).
"""

import re
from modules.logging_config import get_logger

logger = get_logger(__name__)

MAX_MESSAGE_LENGTH = 4000

# ---------------------------------------------------------------------------
# Injection patterns (case-insensitive)
# ---------------------------------------------------------------------------

# English injection phrases
_INJECTION_PHRASES = re.compile(
    r"(?i)"
    r"(?:ignore|forget|disregard|override|bypass)\s+"
    r"(?:all\s+)?(?:previous\s+|prior\s+|above\s+|your\s+)?"
    r"(?:instructions?|prompts?|rules?|context|system\s*(?:message|prompt)?)",
)

# Hindi / Hinglish variants
_INJECTION_PHRASES_HI = re.compile(
    r"(?i)"
    r"(?:apne|apni|sabhi|pichle|purane)\s+"
    r"(?:instructions?|nirdesh|rules?|hidayat)\s+"
    r"(?:bhool\s*jao|bhul\s*jao|ignore\s*karo|hatao|chodo|band\s*karo)",
)

# Role-injection prefixes at start of lines: "system:", "assistant:", "user:"
_ROLE_PREFIX = re.compile(
    r"(?im)^(system|assistant|user)\s*:\s*",
)

# Markdown headings that try to redefine role/instructions
_MD_ROLE_HEADING = re.compile(
    r"(?im)^#+\s+.*(?:system|role|instruction|prompt).*$",
)

# Base64 blobs: 40+ contiguous base64 chars with no spaces
_BASE64_BLOB = re.compile(
    r"(?<!\w)[A-Za-z0-9+/=]{40,}(?!\w)",
)

# RTL / unicode control characters (U+200E-200F, U+202A-202E, U+2066-2069)
_RTL_CONTROL = re.compile(
    r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]",
)


def sanitize_input(text: str) -> tuple[str, list[str]]:
    """Sanitize a WhatsApp message before it reaches the LLM.

    Returns:
        (sanitized_text, flags) where flags is a list of string labels.
        An empty flags list means nothing suspicious was found.
    """
    flags: list[str] = []
    sanitized = text

    # --- length check (flag only; blocking is in is_message_allowed) ---
    if len(sanitized) > MAX_MESSAGE_LENGTH:
        flags.append("length_overflow")

    # --- injection phrases (English) ---
    if _INJECTION_PHRASES.search(sanitized):
        flags.append("injection_attempt")
        sanitized = _INJECTION_PHRASES.sub("", sanitized)

    # --- injection phrases (Hindi/Hinglish) ---
    if _INJECTION_PHRASES_HI.search(sanitized):
        if "injection_attempt" not in flags:
            flags.append("injection_attempt")
        sanitized = _INJECTION_PHRASES_HI.sub("", sanitized)

    # --- role-injection prefixes ---
    if _ROLE_PREFIX.search(sanitized):
        flags.append("role_confusion")
        sanitized = _ROLE_PREFIX.sub("", sanitized)

    # --- markdown heading role redefinition ---
    if _MD_ROLE_HEADING.search(sanitized):
        if "role_confusion" not in flags:
            flags.append("role_confusion")
        sanitized = _MD_ROLE_HEADING.sub("", sanitized)

    # --- base64 blobs ---
    if _BASE64_BLOB.search(sanitized):
        flags.append("base64_blob")
        sanitized = _BASE64_BLOB.sub("[removed:base64]", sanitized)

    # --- excessive RTL / unicode control chars ---
    rtl_matches = _RTL_CONTROL.findall(sanitized)
    if len(rtl_matches) > 10:
        flags.append("excessive_unicode")

    # --- clean up residual whitespace from removals ---
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
    sanitized = sanitized.strip()

    # --- structured logging ---
    if flags:
        logger.warning(
            "input_filter.flagged",
            flags=flags,
            original_length=len(text),
            sanitized_length=len(sanitized),
        )

    return sanitized, flags


def is_message_allowed(text: str) -> tuple[bool, str]:
    """Check whether a message should be processed at all.

    Returns:
        (allowed, reason) — reason is empty string when allowed.
    """
    if len(text) > MAX_MESSAGE_LENGTH:
        return False, f"Message too long ({len(text)} chars, max {MAX_MESSAGE_LENGTH})"

    return True, ""
