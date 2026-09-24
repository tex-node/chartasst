"""Unit tests for :mod:`app.emailer` (SMTP channel)."""

from types import SimpleNamespace

from app import emailer as emailer_module
from app.config import Config
from app.emailer import Emailer, send_email


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.ehlo_calls = 0
        self.started_tls = False
        self.logged = None
        self.quit_called = False
        self.sent = []
        FakeSMTP.instances.append(self)

    def ehlo(self):
        self.ehlo_calls += 1

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.logged = (username, password)

    def send_message(self, message):
        self.sent.append(message)

    def quit(self):
        self.quit_called = True


class BoomSMTP(FakeSMTP):
    def ehlo(self):
        raise OSError("network unreachable")


def _configure(monkeypatch, **overrides):
    values = {
        "SMTP_HOST": "smtp.example.com",
        "SMTP_PORT": 587,
        "SMTP_USERNAME": "sender@example.com",
        "SMTP_PASSWORD": "app-password",
        "SMTP_FROM": "sender@example.com",
        "SMTP_USE_TLS": True,
        "SMTP_USE_SSL": False,
        "NOTIFY_EMAIL_TO": "nodeswavemonitor@gmail.com",
    }
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setattr(Config, key, value)


def _patch_smtp(monkeypatch, smtp=FakeSMTP, smtp_ssl=None):
    FakeSMTP.instances = []
    monkeypatch.setattr(
        emailer_module,
        "smtplib",
        SimpleNamespace(SMTP=smtp, SMTP_SSL=smtp_ssl or smtp),
    )


# --------------------------------------------------------------------------
# Configuration detection
# --------------------------------------------------------------------------
def test_not_configured_without_host(monkeypatch):
    monkeypatch.setattr(Config, "SMTP_HOST", "")
    monkeypatch.setattr(Config, "NOTIFY_EMAIL_TO", "x@y.com")
    assert Emailer().is_configured() is False


def test_not_configured_without_recipient(monkeypatch):
    monkeypatch.setattr(Config, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(Config, "NOTIFY_EMAIL_TO", "")
    assert Emailer().is_configured() is False


def test_configured_with_host_and_recipient(monkeypatch):
    _configure(monkeypatch)
    assert Emailer().is_configured() is True


# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------
def test_send_noop_when_not_configured(monkeypatch):
    monkeypatch.setattr(Config, "SMTP_HOST", "")
    monkeypatch.setattr(Config, "NOTIFY_EMAIL_TO", "x@y.com")
    _patch_smtp(monkeypatch)
    assert Emailer().send("subject", "body") is False
    assert FakeSMTP.instances == []  # never connected


def test_send_success_uses_starttls_and_login(monkeypatch):
    _configure(monkeypatch)
    _patch_smtp(monkeypatch)

    assert Emailer().send("Hello", "Body text") is True

    server = FakeSMTP.instances[0]
    assert server.host == "smtp.example.com"
    assert server.port == 587
    assert server.started_tls is True
    assert server.logged == ("sender@example.com", "app-password")
    assert server.quit_called is True

    message = server.sent[0]
    assert message["Subject"] == "Hello"
    assert message["To"] == "nodeswavemonitor@gmail.com"
    assert "Body text" in message.get_content()


def test_send_uses_ssl_when_configured(monkeypatch):
    _configure(monkeypatch, SMTP_USE_SSL=True, SMTP_USE_TLS=False, SMTP_PORT=465)
    _patch_smtp(monkeypatch)

    assert Emailer().send("s", "b") is True
    server = FakeSMTP.instances[0]
    assert server.port == 465
    assert server.started_tls is False  # SSL, not STARTTLS


def test_send_failure_returns_false(monkeypatch):
    _configure(monkeypatch)
    _patch_smtp(monkeypatch, smtp=BoomSMTP)
    assert Emailer().send("s", "b") is False


def test_send_skips_login_without_username(monkeypatch):
    _configure(monkeypatch, SMTP_USERNAME="", SMTP_FROM="noreply@example.com")
    _patch_smtp(monkeypatch)
    assert Emailer().send("s", "b") is True
    assert FakeSMTP.instances[0].logged is None


def test_module_level_send_email_helper(monkeypatch):
    _configure(monkeypatch)
    _patch_smtp(monkeypatch)
    assert send_email("subj", "body") is True
