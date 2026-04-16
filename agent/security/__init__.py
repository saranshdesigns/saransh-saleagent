"""
Phase 5 — Security & Hardening module.

Provides four security layers:
  1. Rate limiting (Redis-backed, per-phone/per-IP)
  2. Input sanitization (prompt-injection defense)
  3. Output filtering (prevent secret/PII leaks)
  4. Encryption (AES-256-GCM at rest for PII fields)
"""

from agent.security.rate_limit import (
    init_redis,
    close_redis,
    is_inbound_allowed,
    is_outbound_allowed,
    is_ip_allowed,
)
from agent.security.input_filter import sanitize_input, is_message_allowed
from agent.security.output_filter import filter_output
from agent.security.crypto import encrypt, decrypt, is_encrypted

__all__ = [
    "init_redis",
    "close_redis",
    "is_inbound_allowed",
    "is_outbound_allowed",
    "is_ip_allowed",
    "sanitize_input",
    "is_message_allowed",
    "filter_output",
    "encrypt",
    "decrypt",
    "is_encrypted",
]
