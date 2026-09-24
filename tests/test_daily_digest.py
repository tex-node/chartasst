"""Unit tests for :mod:`scripts.daily_digest` (item 4)."""

from scripts import daily_digest

from app.config import Config


HEALTH = {
    "live_trading_effective": True,
    "panic": False,
    "mt5_connected": True,
}
STATS = {
    "date_utc": "2026-09-24",
    "plans_triggered_today": 2,
    "active_plans": 5,
    "stale_plans": 1,
    "unauthorized_count": 3,
    "mt5_uptime_percent": 99.5,
    "top_non_done_retcodes": [
        {"retcode": 10016, "count": 4, "text": "Invalid stops - SL/TP too close."},
        {"retcode": 10004, "count": 2, "text": "Requote."},
    ],
}


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.requested = []

    def get(self, url, headers=None, timeout=None):
        self.requested.append((url, headers))
        return FakeResponse(self.payload)


# --------------------------------------------------------------------------
# format_digest
# --------------------------------------------------------------------------
def test_format_digest_contains_all_sections():
    message = daily_digest.format_digest(HEALTH, STATS)
    assert "daily digest" in message
    assert "2026-09-24" in message
    assert "Live trading: ON" in message
    assert "MT5 uptime today: 99.5%" in message
    assert "Plans triggered today: 2" in message
    assert "Active plans: 5" in message
    assert "Stale plans (>30d): 1" in message
    assert "Unauthorized webhooks: 3" in message
    assert "10016" in message
    assert "Invalid stops" in message


def test_format_digest_marks_kill_switch():
    health = dict(HEALTH, live_trading_effective=False, panic=True)
    message = daily_digest.format_digest(health, STATS)
    assert "OFF" in message
    assert "kill switch" in message


def test_format_digest_handles_no_retcodes():
    message = daily_digest.format_digest(HEALTH, dict(STATS, top_non_done_retcodes=[]))
    assert "No non-DONE order retcodes" in message


def test_format_digest_handles_missing_uptime():
    message = daily_digest.format_digest(HEALTH, dict(STATS, mt5_uptime_percent=None))
    assert "n/a" in message


def test_format_digest_handles_empty_payloads():
    # Must not raise on empty dicts.
    message = daily_digest.format_digest({}, {})
    assert "daily digest" in message


# --------------------------------------------------------------------------
# fetch_json
# --------------------------------------------------------------------------
def test_fetch_json_sends_api_key():
    session = FakeSession({"ok": True})
    data = daily_digest.fetch_json("http://x/stats/today", api_key="secret", session=session)
    assert data == {"ok": True}
    assert session.requested[0][1] == {"X-API-Key": "secret"}


def test_fetch_json_without_key():
    session = FakeSession({"ok": True})
    daily_digest.fetch_json("http://x/health/full", api_key="", session=session)
    assert session.requested[0][1] == {}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def test_parse_args_dry_run():
    assert daily_digest.parse_args(["--dry-run"]).dry_run is True
    assert daily_digest.parse_args(["--url", "http://s:5000"]).url == "http://s:5000"


def test_configure_console_utf8_tolerates_missing_reconfigure(monkeypatch):
    # An object without .reconfigure must not raise.
    monkeypatch.setattr(daily_digest.sys, "stdout", object())
    monkeypatch.setattr(daily_digest.sys, "stderr", object())
    daily_digest._configure_console_utf8()  # should not raise


def test_main_dry_run_prints_without_sending(monkeypatch, capsys):
    monkeypatch.setattr(
        daily_digest, "fetch_json",
        lambda url, api_key="", timeout=10, session=None: HEALTH if "health" in url else STATS,
    )
    sent = []
    monkeypatch.setattr(daily_digest, "send_telegram", lambda *a, **k: sent.append(a) or True)

    code = daily_digest.main(["--dry-run"])
    out = capsys.readouterr().out
    assert code == 0
    assert "daily digest" in out
    assert sent == []  # dry-run sends nothing


def test_send_email_digest_noop_when_unconfigured(monkeypatch):
    monkeypatch.setattr(Config, "SMTP_HOST", "")
    assert daily_digest.send_email_digest("msg", {"date_utc": "2026-09-24"}) is False


def test_main_sends_email(monkeypatch):
    monkeypatch.setattr(
        daily_digest, "fetch_json",
        lambda url, api_key="", timeout=10, session=None: HEALTH if "health" in url else STATS,
    )
    monkeypatch.setattr(daily_digest, "send_telegram", lambda *a, **k: False)
    emailed = []
    monkeypatch.setattr(
        daily_digest, "send_email_digest", lambda m, s: emailed.append(m) or True
    )
    # Email succeeds even though Telegram is off -> exit code 0.
    assert daily_digest.main([]) == 0
    assert emailed
