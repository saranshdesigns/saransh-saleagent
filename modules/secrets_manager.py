"""
Higher-level encryption wrapper for conversation PII.

Encrypts/decrypts sensitive fields in conversation dicts using
the low-level AES-256-GCM crypto module. Handles JSON serialization
for dict/list field values transparently.
"""

import copy
import json

from agent.security.crypto import decrypt, encrypt, is_encrypted
from modules.logging_config import get_logger

logger = get_logger(__name__)

SENSITIVE_FIELDS: list[str] = ["collectedDetails", "agreed_price", "notes"]


def encrypt_conversation_data(conv: dict) -> dict:
    """
    Deep-copy a conversation dict and encrypt all SENSITIVE_FIELDS.

    - Dict/list values are JSON-serialized before encryption.
    - Already-encrypted values (with "enc:v1:" prefix) are skipped.
    - None/missing fields are left untouched.
    """
    result = copy.deepcopy(conv)

    for field in SENSITIVE_FIELDS:
        if field not in result or result[field] is None:
            continue

        value = result[field]

        # Already encrypted — skip
        if isinstance(value, str) and is_encrypted(value):
            continue

        # Serialize non-string values (dicts, lists) to JSON
        if not isinstance(value, str):
            try:
                value = json.dumps(value, ensure_ascii=False)
            except (TypeError, ValueError) as exc:
                logger.error(
                    "encrypt_serialize_failed",
                    field=field,
                    error=str(exc),
                )
                continue

        result[field] = encrypt(value)

    return result


def decrypt_conversation_data(conv: dict) -> dict:
    """
    Deep-copy a conversation dict and decrypt all SENSITIVE_FIELDS.

    - Decrypted JSON strings are parsed back into dicts/lists.
    - Plaintext legacy values pass through unchanged.
    - None/missing fields are left untouched.
    """
    result = copy.deepcopy(conv)

    for field in SENSITIVE_FIELDS:
        if field not in result or result[field] is None:
            continue

        value = result[field]

        if not isinstance(value, str):
            continue  # already a dict/list (legacy unencrypted), pass through

        if is_encrypted(value):
            decrypted = decrypt(value)

            # Try to parse JSON back into structured data
            try:
                parsed = json.loads(decrypted)
                result[field] = parsed
            except (json.JSONDecodeError, TypeError):
                # Plain string value, not JSON — use as-is
                result[field] = decrypted
        # else: plaintext legacy string, leave as-is

    return result


def needs_encryption(conv: dict) -> bool:
    """
    Returns True if any SENSITIVE_FIELD has a value present that is not yet encrypted.
    """
    for field in SENSITIVE_FIELDS:
        if field not in conv or conv[field] is None:
            continue

        value = conv[field]

        # Non-string values (dicts, lists) always need encryption
        if not isinstance(value, str):
            return True

        # String values that aren't encrypted yet need encryption
        if not is_encrypted(value):
            return True

    return False
