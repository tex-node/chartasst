"""Unit tests for :class:`app.notifier.TelegramCommander` (items 2)."""

from app import runtime
from app.config import Config
from app.notifier import TelegramCommander

CHAT_ID = "88888"


class FakeMatcher:
    def __init__(self, plans=None, stale=None):
        self.plans = plans if plans is not None else [
            {"id": "plan_001", "symbol": "EURUSD", "action": "buy",
             "object_name": "Resistance_1.0850", "name": "EURUSD breakout"},
        ]
        self.stale = stale if stale is not None else []

    def get_active_plans(self):
        return list(self.plans)

    def get_stale_plans(self, max_age_days=30):
        return list(self.stale)


class FakeHandler:
    def __init__(self, connected=True):
        self.connected = connected
        self.closed = []

    def is_connected(self):
        return self.connected

    def get_positions(self):
        return {"count": 2, "positions": []}

    def close_position(self, ticket):
        self.closed.append(ticket)
        return {"success": True, "order": ticket}


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"ok": True}

    def json(self):
        return self._payload


class FakeNotifier:
    def __init__(self, updates=None):
        self.sent = []
        self.session = self._Session(updates or [])
        self._stop = None

    def send_message(self, text, chat_id=None):
        self.sent.append(text)
        if self._stop is not None:
            self._stop.set()
        return True

    class _Session:
        def __init__(self, updates):
            self.updates = updates
            self.calls = 0

        def get(self, url, params=None, timeout=None):
            self.calls += 1
            result = self.updates if self.calls == 1 else []
            return FakeResponse(200, {"ok": True, "result": result})


def _commander(handler=None, notifier=None, matcher=None):
    return TelegramCommander(
        matcher or FakeMatcher(),
        handler or FakeHandler(),
        notifier or FakeNotifier(),
    )


def _enable(monkeypatch):
    monkeypatch.setattr(Config, "TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setattr(Config, "TELEGRAM_CHAT_ID", CHAT_ID)


# --------------------------------------------------------------------------
# Authorization
# --------------------------------------------------------------------------
def test_unauthorized_chat_is_ignored(monkeypatch):
    _enable(monkeypatch)
    assert _commander().handle_command("/status", "99999") == ""


def test_no_configured_chat_rejects_all(monkeypatch):
    monkeypatch.setattr(Config, "TELEGRAM_CHAT_ID", "")
    assert _commander().handle_command("/status", CHAT_ID) == ""


def test_non_command_text_is_ignored(monkeypatch):
    _enable(monkeypatch)
    assert _commander().handle_command("hello there", CHAT_ID) == ""


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def test_help_lists_commands(monkeypatch):
    _enable(monkeypatch)
    reply = _commander().handle_command("/help", CHAT_ID)
    for command in ("/status", "/plans", "/stale", "/panic", "/resume", "/close", "/help"):
        assert command in reply


def test_status_reports_state(monkeypatch):
    _enable(monkeypatch)
    reply = _commander().handle_command("/status", CHAT_ID)
    assert "MT5" in reply
    assert "Live trading" in reply
    assert "Open positions: 2" in reply


def test_plans_lists_active(monkeypatch):
    _enable(monkeypatch)
    reply = _commander().handle_command("/plans", CHAT_ID)
    assert "plan_001" in reply
    assert "EURUSD" in reply


def test_stale_empty(monkeypatch):
    _enable(monkeypatch)
    assert "No stale plans" in _commander().handle_command("/stale", CHAT_ID)


def test_stale_lists_plans(monkeypatch):
    _enable(monkeypatch)
    commander = _commander(matcher=FakeMatcher(stale=[{"id": "plan_009", "symbol": "XAUUSD", "action": "buy"}]))
    reply = commander.handle_command("/stale", CHAT_ID)
    assert "plan_009" in reply


def test_panic_engages_kill_switch(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    reply = _commander().handle_command("/panic", CHAT_ID)
    assert "PANIC" in reply
    assert runtime.flags.is_trading_forced_off() is True


def test_resume_clears_kill_switch(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    commander = _commander()
    commander.handle_command("/panic", CHAT_ID)
    reply = commander.handle_command("/resume", CHAT_ID)
    assert "Resumed" in reply
    assert runtime.flags.is_trading_forced_off() is False


def test_resume_notes_env_disabled(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(Config, "LIVE_TRADING", False)
    reply = _commander().handle_command("/resume", CHAT_ID)
    assert "alert-only" in reply


def test_close_requires_argument(monkeypatch):
    _enable(monkeypatch)
    assert "Usage" in _commander().handle_command("/close", CHAT_ID)


def test_close_calls_handler_when_allowed(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    handler = FakeHandler()
    reply = _commander(handler=handler).handle_command("/close 111", CHAT_ID)
    assert handler.closed == [111]
    assert "Closed" in reply


def test_close_blocked_when_trading_disabled(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(Config, "LIVE_TRADING", False)
    handler = FakeHandler()
    reply = _commander(handler=handler).handle_command("/close 111", CHAT_ID)
    assert handler.closed == []  # handler never called
    assert "blocked" in reply.lower()


def test_unknown_command(monkeypatch):
    _enable(monkeypatch)
    assert "Unknown command" in _commander().handle_command("/bogus", CHAT_ID)


def test_botname_suffix_is_stripped(monkeypatch):
    _enable(monkeypatch)
    assert "Active plans" in _commander().handle_command("/plans@MyTradingBot", CHAT_ID)


# --------------------------------------------------------------------------
# Polling loop
# --------------------------------------------------------------------------
def test_get_updates_returns_empty_when_not_configured(monkeypatch):
    monkeypatch.setattr(Config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(Config, "TELEGRAM_CHAT_ID", "")
    assert _commander().get_updates() == []


def test_run_forever_processes_update_and_replies(monkeypatch):
    _enable(monkeypatch)
    update = {
        "update_id": 1,
        "message": {"chat": {"id": int(CHAT_ID)}, "text": "/status"},
    }
    notifier = FakeNotifier(updates=[update])
    commander = _commander(notifier=notifier)

    stop = __import__("threading").Event()
    notifier._stop = stop
    commander.run_forever(poll_interval=0.01, stop_event=stop)

    assert len(notifier.sent) == 1
    assert "MT5" in notifier.sent[0]


def test_run_forever_noop_when_not_configured(monkeypatch):
    monkeypatch.setattr(Config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(Config, "TELEGRAM_CHAT_ID", "")
    notifier = FakeNotifier()
    # Should return immediately without touching the network.
    _commander(notifier=notifier).run_forever(poll_interval=0.01)
    assert notifier.sent == []
