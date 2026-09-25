"""Integration tests exercising the REAL ``PlanMatcher`` hypothesis paths.

These deliberately avoid ``FakeMatcher``/spy stubs. They cover the two defects
that the mock-based suite missed:

1. ``PlanMatcher.match_hypothesis`` / ``evaluate_hypothesis_market`` raising
   ``NameError: normalize_status is not defined``.
2. Symbolic market references (``prev_day_high`` ...) not resolving because the
   active resolver did not read ``market["references"]`` produced by
   :func:`app.market_adapter.build_market_context`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.condition_engine import evaluate_hypothesis
from app.config import Config
from app.market_adapter import build_market_context
from app.plan_matcher import PlanMatcher
from app.server import create_app, run_hypothesis_observer_cycle

API_KEY = "test-secret-key"
HEADERS = {"X-API-Key": API_KEY}


def DT(hour, minute=0):
    return datetime(2026, 9, 25, hour, minute, tzinfo=timezone.utc)


# Two completed H1 bars (last is the current completed bar). The reference
# snapshot then has: close=101, previous_close=99.
ROWS = [
    {"time": DT(10), "open": 100, "high": 102, "low": 98, "close": 99},
    {"time": DT(11), "open": 99, "high": 104, "low": 97, "close": 101},
]
PREV_DAY = [{"time": DT(0), "open": 100, "high": 100.0, "low": 90.0, "close": 95}]
PREV_WEEK = [{"time": DT(0), "open": 100, "high": 105.0, "low": 80.0, "close": 95}]

# prev_day_high=100, prev_day_low=90, prev_week_high=105, prev_week_low=80,
# session_high=104, session_low=97, close=101.
CTX = build_market_context("XAUUSD", "H1", ROWS, PREV_DAY, PREV_WEEK, now=DT(15))


def make_matcher(tmp_path, plans):
    """Build a real PlanMatcher backed by a temporary plans.json."""
    path = tmp_path / "plans.json"
    path.write_text(json.dumps({"plans": plans}), encoding="utf-8")
    return PlanMatcher(plans_path=path)


def hypothesis(conditions, status="watching", hid="hyp_001"):
    return {
        "id": hid,
        "name": "XAUUSD H1 test",
        "symbol": "XAUUSD",
        "timeframe": "H1",
        "action": "buy",
        "hypothesis_status": status,
        "conditions": conditions,
    }


class _CtxHandler:
    """Minimal handler whose get_market_context returns a fixed snapshot."""

    def __init__(self, context):
        self.context = context

    def get_market_context(self, symbol, timeframe):
        return self.context


class _Handler:
    """Minimal MT5 handler for webhook/server integration tests."""

    def is_connected(self):
        return True

    def get_market_context(self, symbol, timeframe):
        return {"symbol": symbol, "timeframe": timeframe, "error": "disabled in test"}

    def get_positions(self):
        return {"count": 0, "positions": []}

    def execute_plan(self, plan, signal):
        return {"success": False, "error": "alert-only"}

    def close_position(self, ticket):
        return {"success": False, "error": "not available in test"}

    def get_retcode_counts(self):
        return {}

    def shutdown(self):
        pass


class _Notifier:
    def __init__(self):
        self.events = []
        self.unplanned = []

    def send_hypothesis_event(self, plan, event_type, signal=None, status=None):
        self.events.append((plan.get("id"), event_type, status))
        return True

    def send_signal_alert(self, *args, **kwargs):
        return True

    def send_unplanned_alert(self, signal):
        self.unplanned.append(signal)
        return True

    def send_startup_message(self, **kwargs):
        return True


# --------------------------------------------------------------------------
# Test 1 - real matcher matching (regression for the NameError)
# --------------------------------------------------------------------------
def test_real_match_hypothesis_returns_plan(tmp_path):
    matcher = make_matcher(
        tmp_path, [hypothesis({"trigger": {"type": "close_above", "timeframe": "H1", "level": 100}})]
    )

    match = matcher.match_hypothesis({"symbol": "XAUUSD", "timeframe": "H1"})

    assert match is not None
    assert match["id"] == "hyp_001"
    # Symbol / timeframe are respected.
    assert matcher.match_hypothesis({"symbol": "EURUSD", "timeframe": "H1"}) is None
    assert matcher.match_hypothesis({"symbol": "XAUUSD", "timeframe": "M15"}) is None


def test_real_match_hypothesis_excludes_terminal(tmp_path):
    matcher = make_matcher(
        tmp_path,
        [hypothesis({"trigger": {"type": "close_above", "timeframe": "H1", "level": 100}},
                    status="invalidated")],
    )
    assert matcher.match_hypothesis({"symbol": "XAUUSD", "timeframe": "H1"}) is None


# --------------------------------------------------------------------------
# Test 2 - real matcher market evaluation (regression for the NameError)
# --------------------------------------------------------------------------
def test_real_evaluate_hypothesis_market(tmp_path):
    conditions = {
        "trigger": {"type": "close_above", "timeframe": "H1", "level": 100},
        "invalidation": {"type": "close_below", "timeframe": "H1", "level": 50},
    }
    matcher = make_matcher(tmp_path, [hypothesis(conditions)])

    results = matcher.evaluate_hypothesis_market(dict(CTX))

    assert len(results) == 1
    assert results[0]["plan"]["id"] == "hyp_001"
    assert results[0]["evaluation"]["matches"] == ["trigger"]
    assert results[0]["evaluation"]["matched"] is True


# --------------------------------------------------------------------------
# Test 3 - previous-day reference (close_above prev_day_high) + inverse
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "ctype,reference,close,expected",
    [
        ("close_above", "prev_day_high", 101, ["trigger"]),  # 101 > 100
        ("close_below", "prev_day_high", 101, []),           # 101 < 100 is false
        ("close_above", "prev_day_low", 101, ["trigger"]),   # 101 > 90
        ("close_below", "prev_day_low", 101, []),            # 101 < 90 is false
    ],
)
def test_prev_day_reference(tmp_path, ctype, reference, close, expected):
    matcher = make_matcher(
        tmp_path,
        [hypothesis({"trigger": {"type": ctype, "timeframe": "H1", "reference": reference}})],
    )
    results = matcher.evaluate_hypothesis_market({**CTX, "close": close, "price": close})
    matches = results[0]["evaluation"]["matches"] if results else []
    assert matches == expected


# --------------------------------------------------------------------------
# Test 4 - previous-week reference
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "ctype,reference,close,expected",
    [
        ("close_below", "prev_week_high", 101, ["trigger"]),  # 101 < 105
        ("close_above", "prev_week_high", 101, []),           # 101 > 105 is false
        ("close_above", "prev_week_low", 101, ["trigger"]),   # 101 > 80
        ("close_below", "prev_week_low", 101, []),            # 101 < 80 is false
    ],
)
def test_prev_week_reference(tmp_path, ctype, reference, close, expected):
    matcher = make_matcher(
        tmp_path,
        [hypothesis({"trigger": {"type": ctype, "timeframe": "H1", "reference": reference}})],
    )
    results = matcher.evaluate_hypothesis_market({**CTX, "close": close, "price": close})
    matches = results[0]["evaluation"]["matches"] if results else []
    assert matches == expected


# --------------------------------------------------------------------------
# Test 5 - session reference
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "ctype,reference,close,expected",
    [
        ("close_below", "session_high", 101, ["trigger"]),  # 101 < 104
        ("close_above", "session_high", 101, []),           # 101 > 104 is false
        ("close_above", "session_low", 101, ["trigger"]),   # 101 > 97
        ("close_below", "session_low", 101, []),            # 101 < 97 is false
    ],
)
def test_session_reference(tmp_path, ctype, reference, close, expected):
    matcher = make_matcher(
        tmp_path,
        [hypothesis({"trigger": {"type": ctype, "timeframe": "H1", "reference": reference}})],
    )
    results = matcher.evaluate_hypothesis_market({**CTX, "close": close, "price": close})
    matches = results[0]["evaluation"]["matches"] if results else []
    assert matches == expected


def test_reference_via_condition_engine_and_explicit_levels():
    # Reference-based condition resolves through the production path.
    ref_hyp = {
        "hypothesis_status": "watching",
        "conditions": {"trigger": {"type": "close_above", "timeframe": "H1", "reference": "prev_day_high"}},
    }
    assert evaluate_hypothesis(ref_hyp, {**CTX, "close": 101})["matches"] == ["trigger"]
    assert evaluate_hypothesis(ref_hyp, {**CTX, "close": 99})["matches"] == []

    # Explicit numeric levels are unaffected by the resolver change.
    level_hyp = {
        "hypothesis_status": "watching",
        "conditions": {"trigger": {"type": "close_above", "timeframe": "H1", "level": 100}},
    }
    assert evaluate_hypothesis(level_hyp, {**CTX, "close": 101})["matches"] == ["trigger"]
    assert evaluate_hypothesis(level_hyp, {**CTX, "close": 99})["matches"] == []


# --------------------------------------------------------------------------
# Test 6 - webhook integration with the REAL PlanMatcher (no 500)
# --------------------------------------------------------------------------
def test_tradingview_hypothesis_webhook_returns_200(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "API_KEY", API_KEY)
    conditions = {
        "trigger": {"type": "close_above", "timeframe": "H1", "level": 100},
        "invalidation": {"type": "close_below", "timeframe": "H1", "level": 95},
    }
    matcher = make_matcher(tmp_path, [hypothesis(conditions)])
    notifier = _Notifier()
    app = create_app(matcher=matcher, handler=_Handler(), notifier=notifier)
    app.testing = True
    client = app.test_client()

    payload = {
        "symbol": "XAUUSD",
        "timeframe": "H1",
        "event": "bar_close",
        "close": 101,
        "price": 101,
        "timestamp": "2026-09-25T11:00:00+00:00",
        "action": "buy",
    }
    resp = client.post("/webhook/tradingview", json=payload, headers=HEADERS)

    assert resp.status_code == 200  # was 500 (NameError) before the fix
    body = resp.get_json()
    assert body["status"] == "observed"
    assert body["matched"] is True
    assert ("hyp_001", "trigger", "developing") in notifier.events
    assert matcher.get_plan("hyp_001")["hypothesis_status"] == "developing"


def test_mt5_hypothesis_webhook_returns_200(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "API_KEY", API_KEY)
    matcher = make_matcher(
        tmp_path, [hypothesis({"trigger": {"type": "close_above", "timeframe": "H1", "level": 100}})]
    )
    app = create_app(matcher=matcher, handler=_Handler(), notifier=_Notifier())
    app.testing = True
    client = app.test_client()

    resp = client.post(
        "/webhook/mt5",
        json={
            "object_name": "Resistance_1.0000",
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "event": "cross",
            "event_type": "trigger",
        },
        headers=HEADERS,
    )

    assert resp.status_code == 200  # was 500 (NameError) before the fix
    body = resp.get_json()
    assert body.get("hypothesis") is True
    assert body["hypothesis_status"] == "developing"


# --------------------------------------------------------------------------
# Test 7 - observer integration through the REAL evaluation path
# --------------------------------------------------------------------------
def test_observer_cycle_uses_real_evaluate_hypothesis_market(tmp_path):
    matcher = make_matcher(
        tmp_path, [hypothesis({"trigger": {"type": "close_above", "timeframe": "H1", "level": 100}})]
    )
    handler = _CtxHandler(dict(CTX))

    results = run_hypothesis_observer_cycle(
        list(matcher.plans), handler, matcher.evaluate_hypothesis_market
    )

    assert ("XAUUSD", "H1") in results
    observations = results[("XAUUSD", "H1")]
    assert observations
    assert observations[0]["evaluation"]["matches"] == ["trigger"]
