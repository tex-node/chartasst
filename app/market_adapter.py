"""Normalize broker/bar data into the market context consumed by ChartAsst."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


TIMEFRAME_MAP = {
    "M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30", "H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1", "W1": "TIMEFRAME_W1",
}


def _dt(value: Any) -> datetime | None:
    try:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _ohlc(rows: list[dict]) -> dict | None:
    valid = [r for r in rows if r.get("high") is not None and r.get("low") is not None]
    if not valid:
        return None
    return {
        "high": max(float(r["high"]) for r in valid),
        "low": min(float(r["low"]) for r in valid),
    }


def build_market_context(symbol: str, timeframe: str, rows: list[dict],
                         previous_day: list[dict] | None = None,
                         previous_week: list[dict] | None = None,
                         now: datetime | None = None,
                         event: str = "bar_close") -> dict:
    """Build a stable observation payload from normalized OHLC bars."""
    now = now or datetime.now(timezone.utc)
    current = rows[-1] if rows else {}
    previous = rows[-2] if len(rows) > 1 else {}

    day = _ohlc(previous_day or [])
    week = _ohlc(previous_week or [])

    session_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    session_rows = [r for r in rows if (_dt(r.get("time")) or now) >= session_start]
    session = _ohlc(session_rows)

    references = {}
    if day:
        references.update(prev_day_high=day["high"], prev_day_low=day["low"])
    if week:
        references.update(prev_week_high=week["high"], prev_week_low=week["low"])
    if session:
        references.update(session_high=session["high"], session_low=session["low"])

    close = float(current["close"]) if current.get("close") is not None else None
    previous_close = float(previous["close"]) if previous.get("close") is not None else None
    current_time = _dt(current.get("time"))

    return {
        "symbol": str(symbol).strip().upper(),
        "timeframe": str(timeframe).strip().upper(),
        "event": event,
        "timestamp": current_time.isoformat() if current_time else now.isoformat(),
        "price": close,
        "close": close,
        "previous_price": previous_close,
        "previous_close": previous_close,
        "references": references,
    }
