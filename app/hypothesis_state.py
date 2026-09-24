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
    timestamp = now_iso()
    event = {"type": event_type, "description": description, "source": source, "metadata": metadata or {}, "timestamp": timestamp}
    hypothesis.setdefault("events", []).append(event)
    hypothesis["updated_at"] = timestamp
    return event
