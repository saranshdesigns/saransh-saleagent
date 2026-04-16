"""
structlog configuration — JSON in prod, pretty in dev.
Correlation IDs flow via contextvars so every log line inside a request carries them.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import uuid
from contextvars import ContextVar
from typing import Any

import structlog

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")
_phone_hash: ContextVar[str] = ContextVar("phone_hash", default="-")


def hash_phone(phone: str) -> str:
    if not phone:
        return "-"
    return hashlib.sha256(phone.encode("utf-8")).hexdigest()[:12]


def new_correlation_id() -> str:
    cid = uuid.uuid4().hex[:12]
    _correlation_id.set(cid)
    return cid


def set_phone_hash(phone: str) -> str:
    h = hash_phone(phone)
    _phone_hash.set(h)
    return h


def _inject_context(_logger: Any, _method: str, event_dict: dict) -> dict:
    event_dict.setdefault("correlation_id", _correlation_id.get())
    event_dict.setdefault("phone_hash", _phone_hash.get())
    return event_dict


def configure_logging() -> None:
    is_dev = os.getenv("DEBUG", "false").lower() == "true"

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=False),
        _inject_context,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if is_dev:
        renderer: Any = structlog.dev.ConsoleRenderer(colors=True)
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        level=logging.INFO,
        stream=sys.stdout,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
