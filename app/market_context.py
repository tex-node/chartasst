"""Resolve symbolic market references at evaluation time.

The resolver deliberately does not invent market levels. Upstream market
adapters must supply explicit context values.
"""
from __future__ import annotations

from typing import Any

REFERENCES = {
    "prev_week_low",
    "prev_week_high",
    "prev_day_low",
    "prev_day_high",
    "session_low",
    "session_high",
}


def resolve_reference(reference: Any, market: dict) -> float | None:
    """Resolve a symbolic reference from market context.

    Prefers the canonical ``market["references"]`` mapping produced by
    :func:`app.market_adapter.build_market_context` (where ``prev_day_high``,
    ``prev_week_low``, ``session_high``, ... live), and falls back to a nested
    ``market["context"]`` mapping or top-level keys for older callers.
    """
    key = str(reference or "").strip().lower()
    if key not in REFERENCES or not isinstance(market, dict):
        return None

    references = market.get("references")
    if isinstance(references, dict) and key in references:
        value = references[key]
    else:
        context = market.get("context")
        if not isinstance(context, dict):
            context = {}
        value = context.get(key, market.get(key))

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_condition(condition: dict, market: dict) -> dict:
    """Copy a condition and resolve its symbolic reference into level."""
    resolved = dict(condition or {})
    reference = resolved.get("reference")
    if reference and str(reference).strip().lower() != "manual":
        level = resolve_reference(reference, market)
        resolved["resolved_level"] = level
        if level is not None:
            resolved["level"] = level
    return resolved
