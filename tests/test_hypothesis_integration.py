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


# --------------------------------------------------------------------------
# PHASE 3 - one consistent lifecycle contract across every entry point
# --------------------------------------------------------------------------
def _mkt(timestamp, **fields):
    return {"timestamp": timestamp, **fields}


def test_process_confirmation_while_watching_is_ignored(tmp_path):
    matcher = make_matcher(
        tmp_path,
        [hypothesis({"confirmation": {"type": "close_above", "timeframe": "H1", "level": 110}})],
    )
    result = matcher.process_hypothesis_event(
        "hyp_001", {"event_type": "confirmation", "market": _mkt("T1", close=115)}
    )
    assert result.get("ignored") is True
    # Confirmation must NOT skip WATCHING -> DEVELOPING.
    assert matcher.get_plan("hyp_001")["hypothesis_status"] == "watching"


def test_process_target_while_watching_is_ignored(tmp_path):
    matcher = make_matcher(
        tmp_path,
        [hypothesis({"target": {"type": "price_above", "timeframe": "H1", "level": 120}})],
    )
    result = matcher.process_hypothesis_event(
        "hyp_001", {"event_type": "target", "market": _mkt("T1", price=200)}
    )
    assert result.get("ignored") is True
    assert matcher.get_plan("hyp_001")["hypothesis_status"] == "watching"


def test_process_full_lifecycle_sequence(tmp_path):
    matcher = make_matcher(
        tmp_path,
        [hypothesis({
            "trigger": {"type": "close_above", "timeframe": "H1", "level": 100},
            "confirmation": {"type": "close_above", "timeframe": "H1", "level": 110},
            "target": {"type": "price_above", "timeframe": "H1", "level": 120},
        })],
    )
    matcher.process_hypothesis_event("hyp_001", {"event_type": "trigger", "market": _mkt("T1", close=105)})
    assert matcher.get_plan("hyp_001")["hypothesis_status"] == "developing"
    matcher.process_hypothesis_event("hyp_001", {"event_type": "confirmation", "market": _mkt("T2", close=115)})
    assert matcher.get_plan("hyp_001")["hypothesis_status"] == "confirmed"
    matcher.process_hypothesis_event("hyp_001", {"event_type": "target", "market": _mkt("T3", price=125)})
    assert matcher.get_plan("hyp_001")["hypothesis_status"] == "completed"


def test_terminal_state_cannot_progress(tmp_path):
    matcher = make_matcher(
        tmp_path,
        [hypothesis({"trigger": {"type": "close_above", "timeframe": "H1", "level": 100}}, status="completed")],
    )
    result = matcher.process_hypothesis_event(
        "hyp_001", {"event_type": "trigger", "market": _mkt("T1", close=105)}
    )
    assert result.get("ignored") is True
    assert matcher.get_plan("hyp_001")["hypothesis_status"] == "completed"


def test_duplicate_market_event_is_idempotent(tmp_path):
    matcher = make_matcher(
        tmp_path,
        [hypothesis({"trigger": {"type": "close_above", "timeframe": "H1", "level": 100}})],
    )
    snap = {"symbol": "XAUUSD", "timeframe": "H1", "event": "bar_close",
            "timestamp": "T1", "close": 105, "price": 105}

    # First observation matches and advances the lifecycle.
    first = matcher.evaluate_hypothesis_market(snap)
    assert len(first) == 1 and first[0]["evaluation"]["matches"] == ["trigger"]
    matcher.process_hypothesis_event("hyp_001", {"event_type": "trigger", "market": snap})
    assert matcher.get_plan("hyp_001")["hypothesis_status"] == "developing"

    # Replay the SAME completed bar: trigger is no longer eligible, so there is
    # no second match, therefore no second transition and no second notification.
    assert matcher.evaluate_hypothesis_market(snap) == []

    # Even a direct re-process is gated to a no-op.
    replay = matcher.process_hypothesis_event("hyp_001", {"event_type": "trigger", "market": snap})
    assert replay.get("ignored") is True
    assert matcher.get_plan("hyp_001")["hypothesis_status"] == "developing"

    # Exactly one market trigger event was recorded.
    trigger_events = [
        e for e in matcher.get_plan("hyp_001")["events"]
        if e.get("type") == "trigger" and e.get("source") == "market"
    ]
    assert len(trigger_events) == 1


def test_invalidation_allowed_from_active_states(tmp_path):
    for status in ("watching", "developing", "confirmed"):
        matcher = make_matcher(
            tmp_path,
            [hypothesis({"invalidation": {"type": "close_below", "timeframe": "H1", "level": 50}}, status=status)],
        )
        matcher.process_hypothesis_event(
            "hyp_001", {"event_type": "invalidation", "market": _mkt("T", close=10)}
        )
        assert matcher.get_plan("hyp_001")["hypothesis_status"] == "invalidated"


def test_paused_is_excluded_from_all_hypothesis_entry_points(tmp_path):
    matcher = make_matcher(
        tmp_path,
        [hypothesis({"trigger": {"type": "close_above", "timeframe": "H1", "level": 100}}, status="paused")],
    )
    # Not matched directly...
    assert matcher.match_hypothesis({"symbol": "XAUUSD", "timeframe": "H1"}) is None
    # ...not evaluated by the market path...
    assert matcher.evaluate_hypothesis_market(
        {"symbol": "XAUUSD", "timeframe": "H1", "event": "bar_close", "close": 105, "timestamp": "T"}
    ) == []
    # ...and its process is ignored (not in an eligible state).
    result = matcher.process_hypothesis_event(
        "hyp_001", {"event_type": "trigger", "market": _mkt("T", close=105)}
    )
    assert result.get("ignored") is True
    assert matcher.get_plan("hyp_001")["hypothesis_status"] == "paused"


def test_raw_webhook_confirmation_does_not_skip_developing(tmp_path, monkeypatch):
    """The webhook raw-event path shares the same gating as the matcher path."""
    monkeypatch.setattr(Config, "API_KEY", API_KEY)
    matcher = make_matcher(
        tmp_path,
        [hypothesis({"confirmation": {"type": "close_above", "timeframe": "H1", "level": 110}})],
    )
    notifier = _Notifier()
    app = create_app(matcher=matcher, handler=_Handler(), notifier=notifier)
    app.testing = True

    resp = app.test_client().post(
        "/webhook/mt5",
        json={
            "object_name": "Resistance_1.0",
            "symbol": "XAUUSD",
            "timeframe": "H1",
            "event": "cross",
            "event_type": "confirmation",
        },
        headers=HEADERS,
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "observed"
    # Confirmation while watching is gated -> status stays watching (never skipped).
    assert body["hypothesis_status"] == "watching"
    assert matcher.get_plan("hyp_001")["hypothesis_status"] == "watching"
    assert notifier.events == []


def test_raw_webhook_paused_hypothesis_is_not_advanced(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "API_KEY", API_KEY)
    matcher = make_matcher(
        tmp_path,
        [hypothesis({"trigger": {"type": "close_above", "timeframe": "H1", "level": 100}}, status="paused")],
    )
    app = create_app(matcher=matcher, handler=_Handler(), notifier=_Notifier())
    app.testing = True

    resp = app.test_client().post(
        "/webhook/mt5",
        json={"object_name": "X", "symbol": "XAUUSD", "timeframe": "H1", "event": "cross", "event_type": "trigger"},
        headers=HEADERS,
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "received" and body["matched"] is False
    assert matcher.get_plan("hyp_001")["hypothesis_status"] == "paused"
