"""Unit tests for :mod:`app.plan_matcher`."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.plan_matcher import STATUS_ACTIVE, STATUS_DISABLED, PlanMatcher


@pytest.fixture
def plans_file(tmp_path):
    """Write a small, controlled plans.json and return its path."""
    data = {
        "plans": [
            {
                "id": "plan_001",
                "name": "EURUSD Resistance Breakout",
                "symbol": "EURUSD",
                "timeframe": "H1",
                "condition": "Break above 1.0850",
                "object_name": "Resistance_1.0850",
                "action": "buy",
                "volume": 0.01,
                "sl_points": 200,
                "tp_points": 400,
                "status": STATUS_ACTIVE,
                "notes": "Wait for close",
            },
            {
                "id": "plan_002",
                "name": "EURUSD Support Breakdown",
                "symbol": "EURUSD",
                "object_name": "Support_1.0800",
                "action": "sell",
                "status": STATUS_ACTIVE,
            },
            {
                "id": "plan_003",
                "name": "Disabled GBPUSD plan",
                "symbol": "GBPUSD",
                "object_name": "GBP_Disabled",
                "action": "buy",
                "status": STATUS_DISABLED,
            },
        ]
    }
    path = tmp_path / "plans.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def matcher(plans_file):
    return PlanMatcher(plans_path=plans_file)


# --------------------------------------------------------------------------
# match()
# --------------------------------------------------------------------------
def test_match_by_symbol_and_action(matcher):
    plan = matcher.match({"symbol": "EURUSD", "action": "buy", "price": 1.0851})
    assert plan is not None
    assert plan["id"] == "plan_001"


def test_match_is_case_insensitive(matcher):
    plan = matcher.match({"symbol": "eurusd", "action": "BUY"})
    assert plan is not None
    assert plan["id"] == "plan_001"


def test_match_sell_action(matcher):
    plan = matcher.match({"symbol": "EURUSD", "action": "sell"})
    assert plan is not None
    assert plan["id"] == "plan_002"


def test_match_direction_maps_to_action(matcher):
    # "below" should be interpreted as a sell.
    plan = matcher.match({"symbol": "EURUSD", "direction": "below"})
    assert plan is not None
    assert plan["id"] == "plan_002"


def test_match_ignores_disabled_plans(matcher):
    # Only a disabled GBPUSD plan exists -> no match.
    assert matcher.match({"symbol": "GBPUSD", "action": "buy"}) is None


def test_match_unknown_symbol_returns_none(matcher):
    assert matcher.match({"symbol": "USDJPY", "action": "buy"}) is None


def test_match_missing_action_returns_none(matcher):
    assert matcher.match({"symbol": "EURUSD"}) is None


def test_match_non_dict_returns_none(matcher):
    assert matcher.match("not a dict") is None
    assert matcher.match(None) is None


# --------------------------------------------------------------------------
# match_by_object()
# --------------------------------------------------------------------------
def test_match_by_object(matcher):
    plan = matcher.match_by_object("Resistance_1.0850")
    assert plan is not None
    assert plan["id"] == "plan_001"


def test_match_by_object_case_insensitive(matcher):
    plan = matcher.match_by_object("support_1.0800")
    assert plan is not None
    assert plan["id"] == "plan_002"


def test_match_by_object_ignores_disabled(matcher):
    assert matcher.match_by_object("GBP_Disabled") is None


def test_match_by_object_unknown(matcher):
    assert matcher.match_by_object("Does_Not_Exist") is None


def test_match_by_object_empty_and_none(matcher):
    assert matcher.match_by_object("") is None
    assert matcher.match_by_object(None) is None


# --------------------------------------------------------------------------
# get_active_plans()
# --------------------------------------------------------------------------
def test_get_active_plans_excludes_disabled(matcher):
    active = matcher.get_active_plans()
    assert len(active) == 2
    assert all(p["status"] == STATUS_ACTIVE for p in active)


# --------------------------------------------------------------------------
# update_plan_status()
# --------------------------------------------------------------------------
def test_update_plan_status_changes_matching(matcher, plans_file):
    assert matcher.update_plan_status("plan_001", "triggered") is True
    # No longer active -> should not match.
    assert matcher.match({"symbol": "EURUSD", "action": "buy"}) is None
    # Persisted to disk.
    on_disk = json.loads(plans_file.read_text(encoding="utf-8"))
    statuses = {p["id"]: p["status"] for p in on_disk["plans"]}
    assert statuses["plan_001"] == "triggered"


def test_update_plan_status_unknown_returns_false(matcher):
    assert matcher.update_plan_status("plan_999", "triggered") is False


# --------------------------------------------------------------------------
# add_plan()
# --------------------------------------------------------------------------
def test_add_plan_assigns_id_and_persists(matcher, plans_file):
    new_plan = matcher.add_plan({"symbol": "usdjpy", "action": "buy"})
    assert new_plan["id"] == "plan_004"
    assert new_plan["symbol"] == "USDJPY"
    assert new_plan["status"] == STATUS_ACTIVE

    on_disk = json.loads(plans_file.read_text(encoding="utf-8"))
    ids = [p["id"] for p in on_disk["plans"]]
    assert "plan_004" in ids
    assert matcher.get_plan("plan_004") is not None


def test_add_plan_rejects_invalid_action(matcher):
    with pytest.raises(ValueError):
        matcher.add_plan({"symbol": "EURUSD", "action": "hold"})


def test_add_plan_rejects_missing_symbol(matcher):
    with pytest.raises(ValueError):
        matcher.add_plan({"action": "buy"})


def test_add_plan_rejects_duplicate_id(matcher):
    with pytest.raises(ValueError):
        matcher.add_plan({"id": "plan_001", "symbol": "EURUSD", "action": "buy"})


# --------------------------------------------------------------------------
# created_at / staleness
# --------------------------------------------------------------------------
def test_add_plan_populates_created_at(matcher):
    plan = matcher.add_plan({"symbol": "NZDUSD", "action": "buy"})
    assert plan["created_at"]
    parsed = PlanMatcher._parse_created_at(plan["created_at"])
    assert parsed is not None
    # Should be very recent.
    assert (datetime.now(timezone.utc) - parsed) < timedelta(minutes=5)


def test_get_stale_plans_flags_old_active_only(tmp_path):
    old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    recent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    data = {
        "plans": [
            {"id": "old_active", "symbol": "EURUSD", "action": "buy",
             "status": "active", "created_at": old},
            {"id": "new_active", "symbol": "EURUSD", "action": "sell",
             "status": "active", "created_at": recent},
            {"id": "old_disabled", "symbol": "EURUSD", "action": "buy",
             "status": "disabled", "created_at": old},
            {"id": "no_date", "symbol": "EURUSD", "action": "buy", "status": "active"},
        ]
    }
    path = tmp_path / "plans.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    m = PlanMatcher(plans_path=path)

    stale_ids = [p["id"] for p in m.get_stale_plans()]
    assert stale_ids == ["old_active"]
    assert m.count_stale_plans() == 1

    # With a 1-day threshold, the 5-day-old active plan is stale too.
    assert m.count_stale_plans(max_age_days=1) == 2


def test_parse_created_at_handles_bad_values():
    assert PlanMatcher._parse_created_at(None) is None
    assert PlanMatcher._parse_created_at("") is None
    assert PlanMatcher._parse_created_at("not-a-date") is None
    # Naive timestamps are treated as UTC.
    parsed = PlanMatcher._parse_created_at("2026-01-01T00:00:00")
    assert parsed is not None and parsed.tzinfo is not None


# --------------------------------------------------------------------------
# reload
# --------------------------------------------------------------------------
def test_reload_reflects_disk_changes(matcher, plans_file):
    # Simulate an external edit.
    data = json.loads(plans_file.read_text(encoding="utf-8"))
    data["plans"].append(
        {"id": "plan_099", "symbol": "AUDUSD", "action": "sell", "status": STATUS_ACTIVE}
    )
    plans_file.write_text(json.dumps(data), encoding="utf-8")

    matcher.reload()
    assert matcher.match({"symbol": "AUDUSD", "action": "sell"}) is not None
