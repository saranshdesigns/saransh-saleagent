"""
AES-256-GCM encryption for PII fields in conversation data.

Uses APP_ENCRYPTION_KEY env var (32-byte base64-encoded key).
All encrypted values are prefixed with "enc:v1:" for identification.
Gracefully degrades to plaintext if key is not configured.
"""

import base64
import os
import re

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from modules.logging_config import get_logger

logger = get_logger(__name__)

PREFIX = "enc:v1:"
_NONCE_SIZE = 12  # 96-bit nonce for AES-GCM


def _get_key() -> bytes | None:
    """Load and decode the encryption key from env. Returns None if unset."""
    raw = os.environ.get("APP_ENCRYPTION_KEY")
    if not raw:
        return None
    try:
        key = base64.b64decode(raw)
        if len(key) != 32:
            logger.error("encryption_key_invalid_length", length=len(key), expected=32)
            return None
        return key
    except Exception as exc:
        logger.error("encryption_key_decode_failed", error=str(exc))
        return None


def encrypt(plaintext: str) -> str:
    """
    Encrypt a plaintext string using AES-256-GCM.

    Returns base64-encoded blob (nonce + ciphertext+tag) prefixed with "enc:v1:".
    If no key is configured, returns plaintext unchanged with a warning.
    """
    key = _get_key()
    if key is None:
        logger.warning("encrypt_skipped_no_key", hint="APP_ENCRYPTION_KEY not set")
        return plaintext

    try:
        nonce = os.urandom(_NONCE_SIZE)
        aesgcm = AESGCM(key)
        ciphertext_and_tag = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        blob = nonce + ciphertext_and_tag
        return PREFIX + base64.b64encode(blob).decode("ascii")
    except Exception as exc:
        logger.error("encrypt_failed", error=str(exc))
        return plaintext


def decrypt(encrypted: str) -> str:
    """
    Decrypt a value produced by encrypt().

    Strips the "enc:v1:" prefix, base64-decodes, splits nonce from
    ciphertext+tag, and decrypts. Returns plaintext on success.
    If no key is configured or input is not encrypted, returns input unchanged.
    """
    if not encrypted.startswith(PREFIX):
        return encrypted

    key = _get_key()
    if key is None:
        logger.warning("decrypt_skipped_no_key", hint="APP_ENCRYPTION_KEY not set")
        return encrypted

    try:
        blob = base64.b64decode(encrypted[len(PREFIX):])
        if len(blob) < _NONCE_SIZE + 16:  # nonce + minimum tag size
            logger.error("decrypt_blob_too_short", length=len(blob))
            return encrypted
        nonce = blob[:_NONCE_SIZE]
        ciphertext_and_tag = blob[_NONCE_SIZE:]
        aesgcm = AESGCM(key)
        plaintext_bytes = aesgcm.decrypt(nonce, ciphertext_and_tag, None)
        return plaintext_bytes.decode("utf-8")
    except Exception as exc:
        logger.error("decrypt_failed", error=str(exc))
        return encrypted


def is_encrypted(value: str) -> bool:
    """
    Heuristic check: value starts with "enc:v1:" followed by valid base64.
    """
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return False
    payload = value[len(PREFIX):]
    if not payload:
        return False
    # Check valid base64 characters (A-Z, a-z, 0-9, +, /, =)
    return bool(re.fullmatch(r"[A-Za-z0-9+/=]+", payload))


def encrypt_dict_values(data: dict, keys_to_encrypt: list[str]) -> dict:
    """
    Encrypt specified keys in a dict. Already-encrypted values are skipped.
    Returns a new dict (shallow copy with encrypted values replaced).
    """
    result = dict(data)
    for key in keys_to_encrypt:
        if key not in result or result[key] is None:
            continue
        value = result[key]
        if isinstance(value, str) and is_encrypted(value):
            continue  # already encrypted
        if not isinstance(value, str):
            value = str(value)
        result[key] = encrypt(value)
    return result


def decrypt_dict_values(data: dict, keys_to_decrypt: list[str]) -> dict:
    """
    Decrypt specified keys in a dict. Plaintext values (legacy rows) pass through.
    Returns a new dict (shallow copy with decrypted values replaced).
    """
    result = dict(data)
    for key in keys_to_decrypt:
        if key not in result or result[key] is None:
            continue
        value = result[key]
        if not isinstance(value, str):
            continue
        if is_encrypted(value):
            result[key] = decrypt(value)
        # else: plaintext legacy value, pass through
    return result
