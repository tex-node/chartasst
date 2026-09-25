"""Safe, deterministic hypothesis condition evaluation."""
from __future__ import annotations

from typing import Any

from app.market_context import resolve_condition


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_level(condition: dict, market: dict) -> float | None:
    level = _num(condition.get("level"))
    if level is None:
        level = _num(condition.get("resolved_level"))
    if level is not None: return level
    reference = str(condition.get("reference", "")).strip().lower()
    refs = market.get("references") or {}
    value = refs.get(reference)
    return _num(value)

def evaluate_condition(condition: dict, market: dict) -> bool:
    if not isinstance(condition, dict) or not isinstance(market, dict):
        return False

    condition = resolve_condition(condition, market)
    kind = str(condition.get("type", "")).strip().lower()
    tf = str(condition.get("timeframe", "")).strip().upper()
    observed_tf = str(market.get("timeframe", "")).strip().upper()
    if tf and observed_tf and tf != observed_tf:
        return False

    level = _num(condition.get("level"))

    # --- simple level comparisons (single-bar, no prior-state gate) ------
    if kind == "price_above":
        price = _num(market.get("price"))
        return price is not None and level is not None and price > level
    if kind == "price_below":
        price = _num(market.get("price"))
        return price is not None and level is not None and price < level
    if kind == "close_above":
        price = _num(market.get("close"))
        return price is not None and level is not None and price > level
    if kind == "close_below":
        price = _num(market.get("close"))
        return price is not None and level is not None and price < level

    # --- break: a decisive close beyond the level, no prior-state gate ---
    # ``break_below`` keeps its established close-below contract; ``break_above``
    # is added as the exact symmetric counterpart.
    if kind == "break_above":
        price = _num(market.get("close"))
        return price is not None and level is not None and price > level
    if kind == "break_below":
        price = _num(market.get("close"))
        return price is not None and level is not None and price < level

    # --- reclaim: a *bar-close* re-take of a level from the other side ----
    # reclaim_above  -> previous completed close was at/below the level AND
    #                   the current close is above it.
    # reclaim_below  -> previous completed close was at/above the level AND
    #                   the current close is below it.
    # Unlike ``break`` it requires prior-state evidence, so it never
    # manufactures a reclaim when the previous close was already past the level.
    if kind in ("reclaim_above", "reclaim_below"):
        current = _num(market.get("close"))
        previous = _num(market.get("previous_close"))
        if previous is None:
            previous = _num(market.get("previous_price"))
        if None in (current, previous, level):
            return False
        if kind == "reclaim_above":
            return previous <= level < current
        return previous >= level > current

    # --- cross: a tick/price transition through the level (distinct fields) -
    if kind == "cross_above":
        price, previous = _num(market.get("price")), _num(market.get("previous_price"))
        return None not in (price, previous, level) and previous <= level < price
    if kind == "cross_below":
        price, previous = _num(market.get("price")), _num(market.get("previous_price"))
        return None not in (price, previous, level) and previous >= level > price

    if kind == "bar_close_above":
        return str(market.get("event", "")).lower() == "bar_close" and _num(market.get("close")) is not None and level is not None and float(market["close"]) > level
    if kind == "bar_close_below":
        return str(market.get("event", "")).lower() == "bar_close" and _num(market.get("close")) is not None and level is not None and float(market["close"]) < level
    if kind == "event":
        return bool(condition.get("event_type")) and str(market.get("event_type", "")).lower() == str(condition["event_type"]).lower()
    return False


def evaluate_hypothesis(hypothesis: dict, market: dict) -> dict:
    """Evaluate conditions in lifecycle order.

    Invalidation is always eligible while a hypothesis is active. Trigger,
    confirmation, and target are gated by the current hypothesis state so a
    target cannot complete an idea that was never confirmed.
    """
    specs = hypothesis.get("conditions") or {}
    status = str(hypothesis.get("hypothesis_status", "watching")).strip().lower()
    eligible = {"invalidation"}
    if status == "watching":
        eligible.add("trigger")
    elif status == "developing":
        eligible.add("confirmation")
    elif status == "confirmed":
        eligible.add("target")

    matches = [
        name for name in ("trigger", "confirmation", "invalidation", "target")
        if name in eligible
        and isinstance(specs.get(name), dict)
        and evaluate_condition(specs[name], market)
    ]
    return {
        "matches": matches,
        "matched": bool(matches),
        "status": status,
        "eligible": sorted(eligible),
    }
