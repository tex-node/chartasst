import pytest

from app.condition_engine import evaluate_condition, evaluate_hypothesis


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


# --------------------------------------------------------------------------
# PHASE 1 - reclaim semantics (bar-close re-take from the other side)
# --------------------------------------------------------------------------
def snapshot(close, previous_close=None, price=None, previous_price=None):
    return {
        "timeframe": "H1",
        "event": "bar_close",
        "close": close,
        "previous_close": previous_close,
        "price": close if price is None else price,
        "previous_price": previous_close if previous_price is None else previous_price,
    }


@pytest.mark.parametrize(
    "previous_close,close,expected",
    [
        (99, 101, True),   # prev below + current above -> reclaim
        (100, 101, True),  # prev equal + current above -> reclaim
        (101, 102, False), # prev above + current above -> NOT a reclaim
        (99, 98, False),   # prev below + current below -> no reclaim
    ],
)
def test_reclaim_above(previous_close, close, expected):
    cond = {"type": "reclaim_above", "level": 100}
    assert evaluate_condition(cond, snapshot(close, previous_close)) is expected


@pytest.mark.parametrize(
    "previous_close,close,expected",
    [
        (101, 99, True),   # prev above + current below -> reclaim
        (100, 99, True),   # prev equal + current below -> reclaim
        (99, 98, False),   # prev below + current below -> no reclaim
        (101, 102, False), # prev above + current above -> NOT a reclaim
    ],
)
def test_reclaim_below(previous_close, close, expected):
    cond = {"type": "reclaim_below", "level": 100}
    assert evaluate_condition(cond, snapshot(close, previous_close)) is expected


def test_reclaim_requires_a_previous_state():
    # No previous bar information -> a reclaim can never be manufactured.
    assert evaluate_condition({"type": "reclaim_above", "level": 100},
                              {"timeframe": "H1", "close": 101}) is False


def test_reclaim_requires_a_level():
    assert evaluate_condition({"type": "reclaim_above"},
                              snapshot(101, 99)) is False


# --------------------------------------------------------------------------
# PHASE 2 - break operators (symmetric, single-bar decisive close)
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "kind,close,expected",
    [
        ("break_above", 101, True),
        ("break_above", 100, False),
        ("break_above", 99, False),
        ("break_below", 99, True),
        ("break_below", 100, False),
        ("break_below", 101, False),
    ],
)
def test_break_operators(kind, close, expected):
    assert evaluate_condition({"type": kind, "level": 100},
                              {"timeframe": "H1", "close": close}) is expected


def test_break_above_does_not_require_prior_state():
    # Unlike reclaim, a break fires whenever the close is decisively beyond the
    # level, even if the previous close was already beyond it.
    m = snapshot(101, previous_close=100.5)
    assert evaluate_condition({"type": "break_above", "level": 100}, m) is True


# --------------------------------------------------------------------------
# cross semantics must not be conflated with reclaim
# --------------------------------------------------------------------------
def test_cross_and_reclaim_are_distinct_fields():
    # previous_price above the level (no tick cross), but previous_close at/below
    # and close above -> a bar-close reclaim with no corresponding price cross.
    m = {"timeframe": "H1", "event": "bar_close",
         "price": 101, "previous_price": 101,   # previous already above -> no cross
         "close": 101, "previous_close": 99}    # previous at/below  -> reclaim
    assert evaluate_condition({"type": "reclaim_above", "level": 100}, m) is True
    assert evaluate_condition({"type": "cross_above", "level": 100}, m) is False


# --------------------------------------------------------------------------
# reclaim obeys lifecycle eligibility like every other operator
# --------------------------------------------------------------------------
def test_reclaim_honours_lifecycle_eligibility():
    m = snapshot(101, previous_close=99)
    as_trigger = {"hypothesis_status": "watching",
                  "conditions": {"trigger": {"type": "reclaim_above", "timeframe": "H1", "level": 100}}}
    assert evaluate_hypothesis(as_trigger, m)["matches"] == ["trigger"]

    # Same condition in the confirmation slot is NOT eligible while watching.
    as_confirmation = {"hypothesis_status": "watching",
                       "conditions": {"confirmation": {"type": "reclaim_above", "timeframe": "H1", "level": 100}}}
    assert evaluate_hypothesis(as_confirmation, m)["matches"] == []

    # ...and it IS eligible while developing.
    developing = {"hypothesis_status": "developing",
                  "conditions": {"confirmation": {"type": "reclaim_above", "timeframe": "H1", "level": 100}}}
    assert evaluate_hypothesis(developing, m)["matches"] == ["confirmation"]
