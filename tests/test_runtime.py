"""Unit tests for :mod:`app.runtime` (kill switch, uptime, trigger log)."""

from app import runtime
from app.config import Config


# --------------------------------------------------------------------------
# RuntimeFlags (kill switch)
# --------------------------------------------------------------------------
def test_effective_true_by_default(monkeypatch):
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    assert runtime.flags.live_trading_effective() is True


def test_force_disable_overrides_env(monkeypatch):
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    previous = runtime.flags.force_disable_trading("test")
    assert previous is False
    assert runtime.flags.is_trading_forced_off() is True
    assert runtime.flags.live_trading_effective() is False


def test_resume_restores_env_value(monkeypatch):
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    runtime.flags.force_disable_trading()
    previous = runtime.flags.resume_trading()
    assert previous is True
    assert runtime.flags.is_trading_forced_off() is False
    assert runtime.flags.live_trading_effective() is True


def test_env_off_always_wins(monkeypatch):
    monkeypatch.setattr(Config, "LIVE_TRADING", False)
    # Even after resume, .env disabled trading -> still not effective.
    runtime.flags.resume_trading()
    assert runtime.flags.live_trading_effective() is False


def test_explicit_env_value_argument(monkeypatch):
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    assert runtime.flags.live_trading_effective(env_value=True) is True
    assert runtime.flags.live_trading_effective(env_value=False) is False


def test_reset_clears_override():
    runtime.flags.force_disable_trading()
    runtime.flags.reset()
    assert runtime.flags.is_trading_forced_off() is False


def test_reason_is_recorded():
    runtime.flags.force_disable_trading("panic")
    assert runtime.flags.reason == "panic"
    runtime.flags.resume_trading()
    assert runtime.flags.reason is None


# --------------------------------------------------------------------------
# UptimeTracker
# --------------------------------------------------------------------------
def test_uptime_none_without_samples():
    assert runtime.uptime.uptime_percent() is None


def test_uptime_percentage():
    runtime.uptime.record(True)
    runtime.uptime.record(True)
    runtime.uptime.record(False)
    runtime.uptime.record(True)
    assert runtime.uptime.uptime_percent() == 75.0
    assert runtime.uptime.sample_count() == 4


def test_uptime_reset():
    runtime.uptime.record(True)
    runtime.uptime.reset()
    assert runtime.uptime.uptime_percent() is None


# --------------------------------------------------------------------------
# TriggerLog
# --------------------------------------------------------------------------
def test_trigger_log_counts_today():
    runtime.trigger_log.record("plan_001")
    runtime.trigger_log.record("plan_002")
    runtime.trigger_log.record("plan_001")
    assert runtime.trigger_log.today_count() == 3
    assert runtime.trigger_log.today_ids() == ["plan_001", "plan_002", "plan_001"]


def test_trigger_log_reset():
    runtime.trigger_log.record("plan_001")
    runtime.trigger_log.reset()
    assert runtime.trigger_log.today_count() == 0
