"""Unit tests for :mod:`app.notifier`.

Network calls are mocked; no real Telegram/Notion traffic is generated.
"""

import requests

from app.config import Config
from app.notifier import Notifier


class FakeResponse:
    """Minimal stand-in for ``requests.Response``."""

    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"ok": True}
        self.text = text or ""

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


SIGNAL = {
    "symbol": "EURUSD",
    "action": "buy",
    "price": 1.0851,
    "timeframe": "H1",
}

PLAN = {
    "id": "plan_001",
    "name": "EURUSD Resistance Breakout",
    "symbol": "EURUSD",
    "timeframe": "H1",
    "condition": "Break above 1.0850",
    "action": "buy",
    "notes": "Wait for close",
}

RESULT_OK = {"success": True, "order": 12345, "price": 1.08512, "sl": 1.08312, "tp": 1.08712}
RESULT_FAIL = {"success": False, "error": "market closed"}


def _enable_telegram(monkeypatch):
    monkeypatch.setattr(Config, "TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(Config, "TELEGRAM_CHAT_ID", "12345")


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------
def test_format_signal_message_contains_key_fields():
    message = Notifier()._format_signal_message(SIGNAL, PLAN, RESULT_OK)
    assert "🟢" in message
    assert "BUY" in message
    assert "EURUSD" in message
    assert "EURUSD Resistance Breakout" in message
    assert "12345" in message  # ticket
    assert "Break above 1.0850" in message


def test_format_signal_message_sell_uses_red_circle():
    plan = dict(PLAN, action="sell")
    signal = dict(SIGNAL, action="sell")
    message = Notifier()._format_signal_message(signal, plan, RESULT_OK)
    assert "🔴" in message
    assert "SELL" in message


def test_format_signal_message_reports_failure():
    message = Notifier()._format_signal_message(SIGNAL, PLAN, RESULT_FAIL)
    assert "market closed" in message
    assert "⚠️" in message


def test_format_unplanned_message():
    message = Notifier()._format_unplanned_message(SIGNAL)
    assert "UNPLANNED SIGNAL" in message
    assert "EURUSD" in message
    assert "no order was placed" in message


def test_format_signal_message_includes_retcode_text():
    result = {"success": False, "error": "retcode=10016 Invalid stops", "retcode_text": "Invalid stops - SL/TP too close to price."}
    message = Notifier()._format_signal_message(SIGNAL, PLAN, result)
    assert "Invalid stops" in message
    assert "❌ Reason:" in message


# --------------------------------------------------------------------------
# Startup notification
# --------------------------------------------------------------------------
def test_format_startup_message():
    message = Notifier()._format_startup_message(
        mt5_connected=True, plan_count=3, live_trading=False
    )
    assert "Trading Assistant started" in message
    assert "connected" in message
    assert "Active plans loaded: 3" in message
    assert "alert-only" in message


def test_send_startup_message_not_configured(monkeypatch):
    monkeypatch.setattr(Config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(Config, "TELEGRAM_CHAT_ID", "")
    assert Notifier().send_startup_message(True, 1, False) is False


def test_send_startup_message_sends_telegram(monkeypatch, mocker):
    _enable_telegram(monkeypatch)
    notifier = Notifier()
    post = mocker.patch.object(notifier.session, "post", return_value=FakeResponse(200, {"ok": True}))
    assert notifier.send_startup_message(True, 2, True) is True
    post.assert_called_once()


# --------------------------------------------------------------------------
# Email channel (alongside Telegram)
# --------------------------------------------------------------------------
def test_send_signal_alert_sends_email(monkeypatch, mocker):
    _enable_telegram(monkeypatch)
    notifier = Notifier()
    mocker.patch.object(notifier.session, "post", return_value=FakeResponse(200, {"ok": True}))
    send = mocker.patch.object(notifier.emailer, "send", return_value=True)

    notifier.send_signal_alert(SIGNAL, PLAN, RESULT_OK)

    send.assert_called_once()
    subject, body = send.call_args.args
    assert "EURUSD" in subject
    assert "EXECUTED" in subject
    assert "EURUSD Resistance Breakout" in body


def test_send_signal_alert_email_reports_failure(monkeypatch, mocker):
    _enable_telegram(monkeypatch)
    notifier = Notifier()
    mocker.patch.object(notifier.session, "post", return_value=FakeResponse(200, {"ok": True}))
    send = mocker.patch.object(notifier.emailer, "send", return_value=True)

    notifier.send_signal_alert(SIGNAL, PLAN, RESULT_FAIL)

    subject, _ = send.call_args.args
    assert "NOT EXECUTED" in subject


def test_send_unplanned_alert_sends_email(monkeypatch, mocker):
    _enable_telegram(monkeypatch)
    notifier = Notifier()
    mocker.patch.object(notifier.session, "post", return_value=FakeResponse(200, {"ok": True}))
    send = mocker.patch.object(notifier.emailer, "send", return_value=True)

    notifier.send_unplanned_alert(SIGNAL)
    send.assert_called_once()
    assert "Unplanned" in send.call_args.args[0]


def test_send_startup_message_sends_email(monkeypatch, mocker):
    _enable_telegram(monkeypatch)
    notifier = Notifier()
    mocker.patch.object(notifier.session, "post", return_value=FakeResponse(200, {"ok": True}))
    send = mocker.patch.object(notifier.emailer, "send", return_value=True)

    notifier.send_startup_message(True, 2, False)
    send.assert_called_once()


def test_email_failure_never_breaks_notification(monkeypatch, mocker):
    _enable_telegram(monkeypatch)
    notifier = Notifier()
    mocker.patch.object(notifier.session, "post", return_value=FakeResponse(200, {"ok": True}))
    mocker.patch.object(notifier.emailer, "send", side_effect=RuntimeError("smtp down"))

    # Telegram still returns True; no exception escapes.
    assert notifier.send_signal_alert(SIGNAL, PLAN, RESULT_OK) is True


def test_email_send_skipped_when_not_configured(monkeypatch):
    monkeypatch.setattr(Config, "SMTP_HOST", "")
    monkeypatch.setattr(Config, "NOTIFY_EMAIL_TO", "x@y.com")
    notifier = Notifier()
    assert notifier._send_email("subject", "body") is False


def test_to_plain_strips_markdown():
    # Removes backslash escapes and asterisks, keeps the literal underscore.
    assert Notifier._to_plain("a\\_b *bold* c") == "a_b bold c"


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------
def test_send_telegram_success(monkeypatch, mocker):
    _enable_telegram(monkeypatch)
    notifier = Notifier()
    post = mocker.patch.object(notifier.session, "post", return_value=FakeResponse(200, {"ok": True}))

    assert notifier._send_telegram("hello") is True
    post.assert_called_once()
    # Verify the payload targets the configured chat and uses Markdown.
    _, kwargs = post.call_args
    assert kwargs["json"]["chat_id"] == "12345"
    assert kwargs["json"]["parse_mode"] == "Markdown"


def test_send_telegram_not_configured(monkeypatch):
    monkeypatch.setattr(Config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(Config, "TELEGRAM_CHAT_ID", "")
    assert Notifier()._send_telegram("hello") is False


def test_send_telegram_api_error_returns_false(monkeypatch, mocker):
    _enable_telegram(monkeypatch)
    notifier = Notifier()
    mocker.patch.object(
        notifier.session,
        "post",
        return_value=FakeResponse(400, {"ok": False, "description": "bad request"}, text="bad request"),
    )
    assert notifier._send_telegram("hello") is False


def test_send_telegram_network_exception_is_swallowed(monkeypatch, mocker):
    _enable_telegram(monkeypatch)
    notifier = Notifier()
    mocker.patch.object(notifier.session, "post", side_effect=requests.RequestException("boom"))
    # Must not raise.
    assert notifier._send_telegram("hello") is False


def test_send_signal_alert_calls_telegram(monkeypatch, mocker):
    _enable_telegram(monkeypatch)
    monkeypatch.setattr(Config, "NOTION_API_KEY", "")
    monkeypatch.setattr(Config, "OBSIDIAN_VAULT_PATH", "")
    notifier = Notifier()
    post = mocker.patch.object(notifier.session, "post", return_value=FakeResponse(200, {"ok": True}))

    assert notifier.send_signal_alert(SIGNAL, PLAN, RESULT_OK) is True
    post.assert_called_once()


# --------------------------------------------------------------------------
# Notion (no-op when disabled)
# --------------------------------------------------------------------------
def test_notion_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(Config, "NOTION_API_KEY", "")
    monkeypatch.setattr(Config, "NOTION_DATABASE_ID", "")
    # Should simply return without raising.
    Notifier()._log_to_notion(SIGNAL, PLAN, RESULT_OK)


# --------------------------------------------------------------------------
# Obsidian
# --------------------------------------------------------------------------
def test_obsidian_writes_daily_note(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "OBSIDIAN_VAULT_PATH", str(tmp_path))

    Notifier()._log_to_obsidian(SIGNAL, PLAN, RESULT_OK)

    log_dir = tmp_path / "Trade_Logs"
    files = list(log_dir.glob("*.md"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "EURUSD" in content
    assert "EXECUTED" in content
    assert "12345" in content
    # Journal is forced to UTC.
    assert "UTC" in content


def test_obsidian_disabled_is_noop(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "OBSIDIAN_VAULT_PATH", "")
    Notifier()._log_to_obsidian(SIGNAL, PLAN, RESULT_OK)
    assert not (tmp_path / "Trade_Logs").exists()


def test_obsidian_failure_is_swallowed(monkeypatch, tmp_path, mocker):
    # Point the vault at a *file* so mkdir/append fails, then ensure no raise.
    bad = tmp_path / "not_a_dir"
    bad.write_text("x", encoding="utf-8")
    monkeypatch.setattr(Config, "OBSIDIAN_VAULT_PATH", str(bad))
    Notifier()._log_to_obsidian(SIGNAL, PLAN, RESULT_OK)
