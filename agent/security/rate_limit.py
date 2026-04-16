"""
Redis-backed per-phone / per-IP rate limiting with graceful degradation.

Buckets:
  - inbound:<phone>   → 20 messages / 60 s
  - outbound:<phone>  → 15 replies  / 60 s
  - ip:<ip>           → 100 requests / 60 s

If Redis is unavailable the limiter FAILS OPEN (returns True = allow).
"""

from __future__ import annotations

import os
import time

import redis.asyncio as aioredis

from modules.logging_config import get_logger, hash_phone

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level Redis handle
# ---------------------------------------------------------------------------
_redis: aioredis.Redis | None = None

# Default bucket settings
INBOUND_MAX = 20
INBOUND_WINDOW = 60
OUTBOUND_MAX = 15
OUTBOUND_WINDOW = 60
IP_MAX = 100
IP_WINDOW = 60

KEY_PREFIX = "rl:"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

async def init_redis() -> None:
    """Connect to Redis.  Safe to call multiple times."""
    global _redis
    if _redis is not None:
        return

    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    try:
        _redis = aioredis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=2,
            retry_on_timeout=True,
        )
        # Verify connectivity
        await _redis.ping()
        logger.info("rate_limit.redis_connected", redis_url=url)
    except Exception:
        logger.warning("rate_limit.redis_unavailable", redis_url=url)
        _redis = None


async def close_redis() -> None:
    """Gracefully close the Redis connection pool."""
    global _redis
    if _redis is not None:
        try:
            await _redis.aclose()
        except Exception:
            pass
        _redis = None
        logger.info("rate_limit.redis_closed")


# ---------------------------------------------------------------------------
# Core check  (sliding-window via sorted set)
# ---------------------------------------------------------------------------

async def check_rate_limit(
    key: str,
    bucket: str,
    max_count: int,
    window_seconds: int,
) -> bool:
    """Return **True** if the request is allowed, **False** if rate-limited.

    Uses a Redis sorted set whose members are timestamps.  On every call we:
      1. Remove entries older than the window.
      2. Count remaining entries.
      3. If under the limit, add a new entry and allow.

    On any Redis error we fail open (allow).
    """
    if _redis is None:
        # Redis never connected — fail open
        return True

    full_key = f"{KEY_PREFIX}{bucket}:{key}"
    now = time.time()
    window_start = now - window_seconds

    try:
        async with _redis.pipeline(transaction=True) as pipe:
            # 1. Trim expired entries
            pipe.zremrangebyscore(full_key, "-inf", window_start)
            # 2. Count current entries
            pipe.zcard(full_key)
            # 3. Add this request (score = timestamp, member = unique ts)
            pipe.zadd(full_key, {str(now): now})
            # 4. Refresh TTL so the key auto-expires after the window
            pipe.expire(full_key, window_seconds + 1)

            results = await pipe.execute()

        current_count = results[1]  # zcard result (before our zadd)

        if current_count >= max_count:
            # Over the limit — remove the entry we just added
            try:
                await _redis.zrem(full_key, str(now))
            except Exception:
                pass

            # Log the hit with hashed identifier
            log_id = hash_phone(key) if bucket in ("inbound", "outbound") else key
            logger.warning(
                "rate_limit.hit",
                phone_hash=log_id,
                bucket=bucket,
                count=current_count,
                max_count=max_count,
                window=window_seconds,
            )
            return False

        return True

    except Exception:
        # Redis error — fail open
        logger.warning("rate_limit.redis_error", bucket=bucket)
        return True


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

async def is_inbound_allowed(phone: str) -> bool:
    """Check per-phone inbound rate limit (20 msgs / 60 s)."""
    return await check_rate_limit(
        key=phone,
        bucket="inbound",
        max_count=INBOUND_MAX,
        window_seconds=INBOUND_WINDOW,
    )


async def is_outbound_allowed(phone: str) -> bool:
    """Check per-phone outbound rate limit (15 replies / 60 s)."""
    return await check_rate_limit(
        key=phone,
        bucket="outbound",
        max_count=OUTBOUND_MAX,
        window_seconds=OUTBOUND_WINDOW,
    )


async def is_ip_allowed(ip: str) -> bool:
    """Check per-IP webhook rate limit (100 requests / 60 s)."""
    return await check_rate_limit(
        key=ip,
        bucket="ip",
        max_count=IP_MAX,
        window_seconds=IP_WINDOW,
    )
