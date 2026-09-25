from app.hypothesis_state import record_event, transition


def test_market_event_is_idempotent_by_timestamp():
    hypothesis = {"hypothesis_status": "watching", "events": []}
    metadata = {"timestamp": "2026-09-25T10:00:00+00:00"}

    first = record_event(hypothesis, "trigger", "Trigger reached", "market", metadata)
    second = record_event(hypothesis, "trigger", "Trigger reached again", "market", metadata)

    assert first == second
    assert len(hypothesis["events"]) == 1


def test_lifecycle_sequence():
    hypothesis = {"hypothesis_status": "watching", "events": []}

    transition(hypothesis, "developing", "trigger")
    assert hypothesis["hypothesis_status"] == "developing"
    transition(hypothesis, "confirmed", "confirmation")
    assert hypothesis["hypothesis_status"] == "confirmed"
    transition(hypothesis, "completed", "target")
    assert hypothesis["hypothesis_status"] == "completed"


def test_invalidation_is_allowed_from_any_active_state():
    for state in ("watching", "developing", "confirmed"):
        hypothesis = {"hypothesis_status": state, "events": []}
        transition(hypothesis, "invalidated", "invalidation")
        assert hypothesis["hypothesis_status"] == "invalidated"
