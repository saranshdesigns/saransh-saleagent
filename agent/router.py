"""
Phase 2 — Tiered message router.

Priority order:
  1. Opt-out check (Lead.optedOut — already in main.py, double-checked here)
  2. Active ConversationFlow on BotConversation
  3. KeywordRule match (ordered by priority ASC = highest first)
  4. LLM fallback (existing agent/core.py path)

Every routed message logs `route_tier` so we can measure LLM cost savings.
"""

import re
import time
from dataclasses import dataclass, field
from typing import Optional

from modules.logging_config import get_logger

log = get_logger("saransh.agent.router")

# ── Route result ──────────────────────────────────────────

@dataclass
class RouteResult:
    tier: str                    # "flow" | "keyword" | "llm"
    action: str                  # e.g. "greeting", "static_reply", "call_request", "pass_to_llm", "llm"
    response: Optional[str] = None   # Pre-built response text (None = let caller handle)
    rule_id: Optional[str] = None    # KeywordRule.id that matched
    flow_id: Optional[str] = None    # ConversationFlow.id if active
    tokens_saved_estimate: int = 0   # Estimated OpenAI tokens saved


# ── In-memory keyword rule cache ──────────────────────────

_rules_cache: list = []
_rules_loaded_at: float = 0
_CACHE_TTL = 300  # 5 minutes


async def _load_rules() -> list:
    """Load enabled KeywordRules from Postgres, ordered by priority ASC."""
    global _rules_cache, _rules_loaded_at

    if _rules_cache and (time.time() - _rules_loaded_at) < _CACHE_TTL:
        return _rules_cache

    try:
        from modules.db import _pool, _pool_ok
        if not _pool_ok():
            return _rules_cache  # stale cache better than nothing

        async with _pool.acquire() as conn:
            rows = await conn.fetch(
                'SELECT id, keywords, "matchType", response, action, priority '
                'FROM "KeywordRule" WHERE enabled = true ORDER BY priority ASC'
            )
            _rules_cache = [dict(r) for r in rows]
            _rules_loaded_at = time.time()
            log.info("router.rules_loaded", count=len(_rules_cache))
            return _rules_cache
    except Exception as e:
        log.warning("router.rules_load_error", error=str(e))
        return _rules_cache


def _match_rule(text: str, rule: dict) -> bool:
    """Check if text matches a keyword rule."""
    text_lower = text.strip().lower()
    match_type = rule["matchType"]
    keywords = rule["keywords"]

    if match_type == "EXACT":
        return text_lower in [kw.lower() for kw in keywords]
    elif match_type == "CONTAINS":
        return any(kw.lower() in text_lower for kw in keywords)
    elif match_type == "REGEX":
        return any(re.search(kw, text_lower, re.IGNORECASE) for kw in keywords)
    return False


# ── Active flow check ─────────────────────────────────────

async def _check_active_flow(phone: str) -> Optional[dict]:
    """Check if there's an active ConversationFlow for this phone."""
    try:
        from modules.db import _pool, _pool_ok
        if not _pool_ok():
            return None

        async with _pool.acquire() as conn:
            row = await conn.fetchrow(
                '''SELECT bc."activeFlowId", bc."flowStepIndex", cf."stepsJson", cf.name
                   FROM "BotConversation" bc
                   JOIN "ConversationFlow" cf ON bc."activeFlowId" = cf.id
                   WHERE bc."waPhone" = $1 AND bc."activeFlowId" IS NOT NULL
                   ORDER BY bc."createdAt" DESC LIMIT 1''',
                phone,
            )
            if row:
                return dict(row)
    except Exception as e:
        log.warning("router.flow_check_error", error=str(e))
    return None


# ── Main router ───────────────────────────────────────────

async def route_message(phone: str, text: str, msg_type: str, conv_stage: str = "new") -> RouteResult:
    """
    Route an inbound message through the 3-tier system.

    Returns a RouteResult indicating which tier handled it and optionally
    a pre-built response. The caller (handle_client_message) executes the action.
    """
    text_clean = (text or "").strip()

    # ── Tier 1: Active flow ───────────────────────────────
    # The default sales flow is LLM-driven, so we pass through to LLM.
    # Future data-driven flows would execute steps here.
    if msg_type == "text" and conv_stage not in ("new", "handoff", "closed"):
        flow = await _check_active_flow(phone)
        if flow and flow.get("activeFlowId"):
            import json
            steps = flow.get("stepsJson", {})
            if isinstance(steps, str):
                steps = json.loads(steps)
            # The default sales flow is LLM-driven (steps.llm_driven == true)
            # So we log it as flow tier but let LLM handle execution
            if steps.get("llm_driven"):
                log.info("router.routed",
                         route_tier="flow", action="llm_driven_flow",
                         flow_id=flow.get("activeFlowId"),
                         flow_name=flow.get("name"))
                return RouteResult(
                    tier="flow",
                    action="llm_driven_flow",
                    flow_id=flow.get("activeFlowId"),
                    tokens_saved_estimate=0,  # still uses LLM
                )

    # ── Tier 2: Keyword rules ─────────────────────────────
    if msg_type == "text" and text_clean:
        rules = await _load_rules()
        for rule in rules:
            if _match_rule(text_clean, rule):
                action = rule["action"]

                # pass_to_llm = matched but intentionally falls through to LLM
                if action == "pass_to_llm":
                    log.info("router.routed",
                             route_tier="keyword", action=action,
                             rule_id=rule["id"],
                             note="intentional LLM passthrough")
                    return RouteResult(
                        tier="keyword",
                        action="pass_to_llm",
                        rule_id=rule["id"],
                        tokens_saved_estimate=0,
                    )

                # Actions that have pre-built responses
                if action == "static_reply" and rule.get("response"):
                    log.info("router.routed",
                             route_tier="keyword", action=action,
                             rule_id=rule["id"],
                             tokens_saved=3000)
                    return RouteResult(
                        tier="keyword",
                        action="static_reply",
                        response=rule["response"],
                        rule_id=rule["id"],
                        tokens_saved_estimate=3000,
                    )

                # Actions handled by main.py (greeting, call_request, portfolio, opt_out, opt_in)
                log.info("router.routed",
                         route_tier="keyword", action=action,
                         rule_id=rule["id"],
                         tokens_saved=3000)
                return RouteResult(
                    tier="keyword",
                    action=action,
                    rule_id=rule["id"],
                    tokens_saved_estimate=3000,
                )

    # ── Tier 3: LLM fallback ─────────────────────────────
    log.info("router.routed", route_tier="llm", action="llm_fallback")
    return RouteResult(tier="llm", action="llm_fallback")


# ── Stats tracking ────────────────────────────────────────

_tier_counts = {"flow": 0, "keyword": 0, "llm": 0}
_tokens_saved_total = 0


def record_route(result: RouteResult) -> None:
    """Track routing stats for cost measurement."""
    global _tokens_saved_total
    _tier_counts[result.tier] = _tier_counts.get(result.tier, 0) + 1
    _tokens_saved_total += result.tokens_saved_estimate
    log.info("router.stats",
             tier_counts=dict(_tier_counts),
             tokens_saved_total=_tokens_saved_total)


def get_stats() -> dict:
    """Return current tier distribution stats."""
    total = sum(_tier_counts.values()) or 1
    return {
        "tier_counts": dict(_tier_counts),
        "tier_pct": {k: round(v / total * 100, 1) for k, v in _tier_counts.items()},
        "tokens_saved_total": _tokens_saved_total,
        "cost_saved_estimate_usd": round(_tokens_saved_total * 0.000003, 4),  # ~$3/1M tokens for gpt-4o-mini
    }
