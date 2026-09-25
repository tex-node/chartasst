from app.condition_engine import evaluate_hypothesis


def hypothesis(status="watching"):
    return {
        "hypothesis_status": status,
        "conditions": {
            "trigger": {"type": "bar_close_above", "timeframe": "H1", "level": 100},
            "confirmation": {"type": "bar_close_above", "timeframe": "H1", "level": 110},
            "invalidation": {"type": "bar_close_below", "timeframe": "H1", "level": 95},
            "target": {"type": "price_above", "timeframe": "H1", "level": 120},
        },
    }


def market(close, event="bar_close", price=None):
    return {"timeframe": "H1", "event": event, "close": close, "price": close if price is None else price}


def test_trigger_is_only_eligible_while_watching():
    result = evaluate_hypothesis(hypothesis("watching"), market(101))
    assert result["matches"] == ["trigger"]


def test_confirmation_requires_developing_state():
    result = evaluate_hypothesis(hypothesis("watching"), market(111))
    assert "confirmation" not in result["matches"]
    result = evaluate_hypothesis(hypothesis("developing"), market(111))
    assert result["matches"] == ["confirmation"]


def test_target_requires_confirmed_state():
    result = evaluate_hypothesis(hypothesis("developing"), market(121, event="tick"))
    assert "target" not in result["matches"]
    result = evaluate_hypothesis(hypothesis("confirmed"), market(121, event="tick"))
    assert result["matches"] == ["target"]


def test_invalidation_remains_eligible():
    for state in ("watching", "developing", "confirmed"):
        result = evaluate_hypothesis(hypothesis(state), market(94))
        assert result["matches"] == ["invalidation"]
