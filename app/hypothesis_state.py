"""Hypothesis lifecycle helpers kept separate from legacy execution status."""
from __future__ import annotations
from datetime import datetime, timezone

STATES = ("watching", "developing", "confirmed", "completed", "invalidated", "expired", "paused")
TRANSITIONS = {
    "watching": {"developing", "confirmed", "invalidated", "expired", "paused"},
    "developing": {"confirmed", "invalidated", "expired", "paused"},
    "confirmed": {"completed", "invalidated", "expired", "paused"},
    "completed": set(),
    "invalidated": set(),
    "expired": {"watching"},
    "paused": {"watching", "expired"},
}

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def normalize_status(value):
    value = str(value or "").strip().lower()
    return {"active": "watching", "triggered": "confirmed", "disabled": "paused"}.get(value, value)


def extract_market_timestamp(metadata):
    """Best-effort market observation timestamp from an event/metadata mapping.

    Handles the actual persisted shapes so duplicate detection works:

    * ``process_hypothesis_event`` stores the event *wrapper* as ``metadata``,
      so the timestamp lives at ``metadata["market"]["timestamp"]``;
    * older/flatter records (and raw signal payloads) may carry it directly as
      ``metadata["timestamp"]`` / ``metadata["bar_timestamp"]``.

    Returns ``None`` when no market timestamp is present.
    """
    if not isinstance(metadata, dict):
        return None
    timestamp = metadata.get("timestamp") or metadata.get("bar_timestamp")
    if timestamp:
        return timestamp
    inner = metadata.get("market")
    if isinstance(inner, dict):
        return inner.get("timestamp") or inner.get("bar_timestamp")
    return None

def can_transition(current, target):
    current, target = normalize_status(current), normalize_status(target)
    return current == target or target in TRANSITIONS.get(current, set())

def transition(hypothesis, target, reason="", source="user", metadata=None):
    current, target = normalize_status(hypothesis.get("hypothesis_status", "watching")), normalize_status(target)
    if not can_transition(current, target):
        raise ValueError(f"Invalid hypothesis transition: {current} -> {target}")
    timestamp = now_iso()
    hypothesis["hypothesis_status"] = target
    hypothesis.setdefault("events", []).append({
        "type": "state_change", "from": current, "to": target,
        "reason": reason, "source": source, "metadata": metadata or {}, "timestamp": timestamp,
    })
    hypothesis["updated_at"] = timestamp
    return hypothesis

def record_event(hypothesis, event_type, description="", source="market", metadata=None):
    """Record an event unless the same market observation was already recorded."""
    metadata = metadata or {}
    market_timestamp = extract_market_timestamp(metadata)
    if source == "market" and market_timestamp:
        for existing in reversed(hypothesis.get("events", [])):
            if existing.get("source") != "market" or existing.get("type") != event_type:
                continue
            existing_market_timestamp = extract_market_timestamp(existing.get("metadata"))
            if existing_market_timestamp == market_timestamp:
                return existing

    timestamp = now_iso()
    event = {"type": event_type, "description": description, "source": source, "metadata": metadata, "timestamp": timestamp}
    hypothesis.setdefault("events", []).append(event)
    hypothesis["updated_at"] = timestamp
    return event
