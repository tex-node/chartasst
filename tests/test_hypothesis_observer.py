"""Tests for the hypothesis observer cycle (``app/server.py``).

Behavioral contract under test:

    One market-context snapshot per unique ``(symbol, timeframe)`` per observer
    cycle, shared by every eligible hypothesis in that group; a completed bar is
    evaluated at most once per ``(symbol, timeframe, timestamp)``.

These exercise :func:`app.server.run_hypothesis_observer_cycle` (a single cycle)
rather than the background thread, so they are deterministic.
"""

from __future__ import annotations

import threading
from collections import Counter

from app.server import (
    bar_observation_key,
    group_observation_hypotheses,
    run_hypothesis_observer_cycle,
)


def hypothesis(hid, symbol, timeframe, status="watching"):
    """A minimal hypothesis plan as stored in plans.json."""
    return {
        "id": hid,
        "symbol": symbol,
        "timeframe": timeframe,
        "hypothesis_status": status,
    }


def bar_context(symbol, timeframe, timestamp, close=100.0):
    """A completed-bar market-context snapshot."""
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "event": "bar_close",
        "timestamp": timestamp,
        "close": close,
        "price": close,
    }


class SpyHandler:
    """Stands in for MT5Handler; records get_market_context calls."""

    def __init__(self, contexts=None):
        self.calls: list[tuple[str, str]] = []
        self._contexts = contexts or {}

    def set_context(self, symbol, timeframe, context):
        self._contexts[(symbol.upper(), timeframe.upper())] = context

    def get_market_context(self, symbol, timeframe):
        key = (str(symbol).strip().upper(), str(timeframe).strip().upper())
        self.calls.append(key)
        context = self._contexts.get(key)
        if context is None:
            return {"error": "no context configured", "symbol": symbol, "timeframe": timeframe}
        return context


class EvaluateSpy:
    """Callable stand-in for the server's ``_evaluate_hypotheses`` closure."""

    def __init__(self, observations=None):
        self.calls: list[tuple[str, str]] = []
        self.contexts: list[dict] = []
        self._observations = observations or {}

    def __call__(self, context):
        key = (
            str(context.get("symbol", "")).strip().upper(),
            str(context.get("timeframe", "")).strip().upper(),
        )
        self.calls.append(key)
        self.contexts.append(context)
        return list(self._observations.get(key, []))


# --------------------------------------------------------------------------
# Test A - shared context fetch (one per group, not per hypothesis)
# --------------------------------------------------------------------------
def test_one_context_fetch_per_symbol_timeframe_group():
    plans = [
        hypothesis("a", "XAUUSD", "H1"),
        hypothesis("b", "XAUUSD", "H1"),
        hypothesis("c", "XAUUSD", "H1"),
        hypothesis("d", "EURUSD", "H1"),
        hypothesis("e", "EURUSD", "H1"),
    ]
    handler = SpyHandler(
        {
            ("XAUUSD", "H1"): bar_context("XAUUSD", "H1", "2026-09-25T10:00:00+00:00"),
            ("EURUSD", "H1"): bar_context("EURUSD", "H1", "2026-09-25T10:00:00+00:00"),
        }
    )
    spy = EvaluateSpy()

    results = run_hypothesis_observer_cycle(plans, handler, spy)

    counts = Counter(handler.calls)
    assert counts[("XAUUSD", "H1")] == 1  # 3 hypotheses, 1 fetch
    assert counts[("EURUSD", "H1")] == 1  # 2 hypotheses, 1 fetch
    assert len(handler.calls) == 2

    # The whole group is evaluated with a single call per group.
    assert len(spy.calls) == 2
    assert set(results) == {("XAUUSD", "H1"), ("EURUSD", "H1")}


# --------------------------------------------------------------------------
# Test B - shared snapshot
# --------------------------------------------------------------------------
def test_group_is_evaluated_with_the_single_fetched_snapshot():
    xau_snapshot = bar_context("XAUUSD", "H1", "2026-09-25T10:00:00+00:00")
    handler = SpyHandler({("XAUUSD", "H1"): xau_snapshot})
    spy = EvaluateSpy()

    plans = [
        hypothesis("a", "XAUUSD", "H1"),
        hypothesis("b", "XAUUSD", "H1"),
        hypothesis("c", "XAUUSD", "H1"),
    ]
    group = group_observation_hypotheses(plans)[("XAUUSD", "H1")]
    assert len(group) == 3

    run_hypothesis_observer_cycle(plans, handler, spy)

    # Exactly one evaluation call for the group...
    assert spy.calls == [("XAUUSD", "H1")]
    # ...and it received the exact snapshot object returned by the single fetch.
    assert spy.contexts[0] is xau_snapshot


# --------------------------------------------------------------------------
# Test C - duplicate bar suppression
# --------------------------------------------------------------------------
def test_duplicate_completed_bar_is_evaluated_once():
    evaluated_bars: set = set()
    evaluated_bars_lock = threading.Lock()
    plans = [hypothesis("a", "XAUUSD", "H1")]
    handler = SpyHandler(
        {("XAUUSD", "H1"): bar_context("XAUUSD", "H1", "2026-09-25T10:00:00+00:00")}
    )
    spy = EvaluateSpy()

    first = run_hypothesis_observer_cycle(plans, handler, spy, evaluated_bars, evaluated_bars_lock)
    second = run_hypothesis_observer_cycle(plans, handler, spy, evaluated_bars, evaluated_bars_lock)

    assert ("XAUUSD", "H1") in first      # cycle 1 @ T -> evaluated
    assert ("XAUUSD", "H1") not in second  # cycle 2 @ T -> skipped
    assert spy.calls == [("XAUUSD", "H1")]

    # cycle 3 @ T+1 -> evaluated again
    handler.set_context("XAUUSD", "H1", bar_context("XAUUSD", "H1", "2026-09-25T11:00:00+00:00"))
    third = run_hypothesis_observer_cycle(plans, handler, spy, evaluated_bars, evaluated_bars_lock)

    assert ("XAUUSD", "H1") in third
    assert spy.calls == [("XAUUSD", "H1"), ("XAUUSD", "H1")]


def test_non_bar_event_is_never_deduplicated():
    evaluated_bars: set = set()
    evaluated_bars_lock = threading.Lock()
    plans = [hypothesis("a", "XAUUSD", "H1")]
    handler = SpyHandler(
        {("XAUUSD", "H1"): {"symbol": "XAUUSD", "timeframe": "H1",
                            "event": "tick", "timestamp": "2026-09-25T10:00:00+00:00"}}
    )
    spy = EvaluateSpy()

    run_hypothesis_observer_cycle(plans, handler, spy, evaluated_bars, evaluated_bars_lock)
    run_hypothesis_observer_cycle(plans, handler, spy, evaluated_bars, evaluated_bars_lock)

    assert len(spy.calls) == 2  # ticks are always evaluated


# --------------------------------------------------------------------------
# Test D - terminal/paused filtering
# --------------------------------------------------------------------------
def test_terminal_and_paused_hypotheses_are_excluded():
    plans = [
        hypothesis("t1", "XAUUSD", "H1", status="invalidated"),
        hypothesis("t2", "XAUUSD", "H1", status="completed"),
        hypothesis("t3", "XAUUSD", "H1", status="expired"),
        hypothesis("t4", "XAUUSD", "H1", status="paused"),
    ]
    handler = SpyHandler({("XAUUSD", "H1"): bar_context("XAUUSD", "H1", "T")})
    spy = EvaluateSpy()

    results = run_hypothesis_observer_cycle(plans, handler, spy)

    assert handler.calls == []  # no eligible hypotheses -> no fetch at all
    assert spy.calls == []
    assert results == {}


def test_active_hypotheses_remain_eligible():
    plans = [
        hypothesis("a1", "XAUUSD", "H1", status="watching"),
        hypothesis("a2", "XAUUSD", "H1", status="developing"),
        hypothesis("a3", "XAUUSD", "H1", status="confirmed"),
    ]
    handler = SpyHandler({("XAUUSD", "H1"): bar_context("XAUUSD", "H1", "T")})
    spy = EvaluateSpy()

    results = run_hypothesis_observer_cycle(plans, handler, spy)

    assert handler.calls == [("XAUUSD", "H1")]
    assert ("XAUUSD", "H1") in results


def test_plans_without_hypothesis_status_are_ignored():
    plans = [{"id": "legacy", "symbol": "XAUUSD", "timeframe": "H1"}]
    handler = SpyHandler({("XAUUSD", "H1"): bar_context("XAUUSD", "H1", "T")})
    spy = EvaluateSpy()

    results = run_hypothesis_observer_cycle(plans, handler, spy)

    assert results == {}
    assert handler.calls == []


# --------------------------------------------------------------------------
# Test E - mixed groups
# --------------------------------------------------------------------------
def test_mixed_groups_are_each_fetched_once():
    plans = [
        hypothesis("a", "XAUUSD", "H1"),
        hypothesis("b", "XAUUSD", "H1"),
        hypothesis("c", "EURUSD", "H1"),
        hypothesis("d", "GBPUSD", "M15"),
        hypothesis("e", "GBPUSD", "M15"),
        hypothesis("f", "GBPUSD", "M15"),
    ]
    handler = SpyHandler(
        {
            ("XAUUSD", "H1"): bar_context("XAUUSD", "H1", "T"),
            ("EURUSD", "H1"): bar_context("EURUSD", "H1", "T"),
            ("GBPUSD", "M15"): bar_context("GBPUSD", "M15", "T"),
        }
    )
    spy = EvaluateSpy()

    results = run_hypothesis_observer_cycle(plans, handler, spy)

    assert Counter(handler.calls) == {
        ("XAUUSD", "H1"): 1,
        ("EURUSD", "H1"): 1,
        ("GBPUSD", "M15"): 1,
    }
    assert set(spy.calls) == {("XAUUSD", "H1"), ("EURUSD", "H1"), ("GBPUSD", "M15")}
    assert set(results) == {("XAUUSD", "H1"), ("EURUSD", "H1"), ("GBPUSD", "M15")}


# --------------------------------------------------------------------------
# Supporting behavior
# --------------------------------------------------------------------------
def test_context_error_skips_group_without_evaluation():
    handler = SpyHandler({})  # returns an error context for every group
    spy = EvaluateSpy()

    results = run_hypothesis_observer_cycle([hypothesis("a", "XAUUSD", "H1")], handler, spy)

    assert results == {}
    assert spy.calls == []


def test_bar_observation_key_normalization():
    assert bar_observation_key("xauusd", "h1", {"event": "bar_close", "timestamp": "T"}) == (
        "XAUUSD",
        "H1",
        "T",
    )
    assert bar_observation_key("XAUUSD", "H1", {"event": "bar_closed", "timestamp": "T"}) == (
        "XAUUSD",
        "H1",
        "T",
    )
    assert bar_observation_key("XAUUSD", "H1", {"event": "tick", "timestamp": "T"}) is None
    assert bar_observation_key("XAUUSD", "H1", {"event": "bar_close"}) is None


# --------------------------------------------------------------------------
# PHASE 4 - a failure on one group must not abort the rest of the cycle
# --------------------------------------------------------------------------
class _RaisingCtxHandler:
    """get_market_context raises for one symbol, succeeds for the others."""

    def __init__(self, fail_symbol, context):
        self.fail_symbol = fail_symbol.upper()
        self.context = context

    def get_market_context(self, symbol, timeframe):
        if symbol.upper() == self.fail_symbol:
            raise RuntimeError("boom-fetch")
        return self.context


class _BySymbolHandler:
    """Returns a distinct context per (symbol, timeframe)."""

    def __init__(self, mapping):
        self.mapping = mapping

    def get_market_context(self, symbol, timeframe):
        return self.mapping[(symbol.upper(), timeframe.upper())]


def test_context_exception_is_isolated_per_group(caplog):
    caplog.set_level("ERROR", logger="app.server")
    plans = [hypothesis("x", "XAUUSD", "H1"), hypothesis("e", "EURUSD", "H1")]
    ok_context = {"symbol": "EURUSD", "timeframe": "H1", "event": "bar_close",
                  "timestamp": "2026-09-25T11:00:00+00:00", "close": 101, "price": 101}
    handler = _RaisingCtxHandler("XAUUSD", ok_context)
    spy = EvaluateSpy()

    results = run_hypothesis_observer_cycle(plans, handler, spy)

    # The failing group is skipped; the healthy group is still evaluated.
    assert ("XAUUSD", "H1") not in results
    assert ("EURUSD", "H1") in results
    assert spy.calls == [("EURUSD", "H1")]
    assert any("context fetch failed for XAUUSD" in r.getMessage() and "boom-fetch" in r.getMessage()
               for r in caplog.records)


def test_evaluation_exception_is_isolated_per_group(caplog):
    caplog.set_level("ERROR", logger="app.server")
    plans = [hypothesis("x", "XAUUSD", "H1"), hypothesis("e", "EURUSD", "H1")]
    ctxs = {
        ("XAUUSD", "H1"): {"symbol": "XAUUSD", "timeframe": "H1", "event": "bar_close",
                           "timestamp": "2026-09-25T10:00:00+00:00", "close": 101, "price": 101},
        ("EURUSD", "H1"): {"symbol": "EURUSD", "timeframe": "H1", "event": "bar_close",
                           "timestamp": "2026-09-25T10:00:00+00:00", "close": 101, "price": 101},
    }

    def evaluate(context):
        if context["symbol"] == "XAUUSD":
            raise RuntimeError("boom-eval")
        return []

    results = run_hypothesis_observer_cycle(plans, _BySymbolHandler(ctxs), evaluate)

    assert ("XAUUSD", "H1") not in results
    assert ("EURUSD", "H1") in results
    assert any("evaluation failed for XAUUSD" in r.getMessage() and "boom-eval" in r.getMessage()
               for r in caplog.records)


def test_context_error_dict_still_isolates_group():
    # An error-returning context (no exception) must not break sibling groups.
    plans = [hypothesis("x", "XAUUSD", "H1"), hypothesis("e", "EURUSD", "H1")]
    handler = _BySymbolHandler({
        ("XAUUSD", "H1"): {"symbol": "XAUUSD", "timeframe": "H1", "error": "not connected"},
        ("EURUSD", "H1"): {"symbol": "EURUSD", "timeframe": "H1", "event": "bar_close",
                           "timestamp": "T", "close": 101, "price": 101},
    })
    results = run_hypothesis_observer_cycle(plans, handler, EvaluateSpy())
    assert ("XAUUSD", "H1") not in results
    assert ("EURUSD", "H1") in results
