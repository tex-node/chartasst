"""Safe, deterministic hypothesis condition evaluation."""
from __future__ import annotations
from typing import Any

def _num(value: Any) -> float | None:
    try: return float(value)
    except (TypeError, ValueError): return None

def evaluate_condition(condition: dict, market: dict) -> bool:
    if not isinstance(condition, dict) or not isinstance(market, dict): return False
    kind = str(condition.get("type", "")).strip().lower()
    tf = str(condition.get("timeframe", "")).strip().upper()
    observed_tf = str(market.get("timeframe", "")).strip().upper()
    if tf and observed_tf and tf != observed_tf: return False
    if kind in {"price_above","close_above","reclaim_above"}:
        price = _num(market.get("close") if kind != "price_above" else market.get("price"))
        level = _num(condition.get("level"))
        return price is not None and level is not None and price > level
    if kind in {"price_below","close_below","break_below"}:
        price = _num(market.get("close") if kind != "price_below" else market.get("price"))
        level = _num(condition.get("level"))
        return price is not None and level is not None and price < level
    if kind == "cross_above":
        price, previous, level = _num(market.get("price")), _num(market.get("previous_price")), _num(condition.get("level"))
        return None not in (price, previous, level) and previous <= level < price
    if kind == "cross_below":
        price, previous, level = _num(market.get("price")), _num(market.get("previous_price")), _num(condition.get("level"))
        return None not in (price, previous, level) and previous >= level > price
    if kind == "bar_close_above":
        return str(market.get("event", "")).lower() == "bar_close" and (_num(market.get("close")) is not None) and (_num(condition.get("level")) is not None) and float(market["close"]) > float(condition["level"])
    if kind == "bar_close_below":
        return str(market.get("event", "")).lower() == "bar_close" and (_num(market.get("close")) is not None) and (_num(condition.get("level")) is not None) and float(market["close"]) < float(condition["level"])
    if kind == "event":
        return bool(condition.get("event_type")) and str(market.get("event_type","")).lower() == str(condition["event_type"]).lower()
    return False

def evaluate_hypothesis(hypothesis: dict, market: dict) -> dict:
    specs = hypothesis.get("conditions") or {}
    matches = [name for name in ("trigger","confirmation","invalidation","target")
               if isinstance(specs.get(name), dict) and evaluate_condition(specs[name], market)]
    return {"matches": matches, "matched": bool(matches)}
