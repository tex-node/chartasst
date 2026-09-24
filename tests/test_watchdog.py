"""Unit tests for :mod:`scripts.watchdog`.

Uses a fake HTTP session so no real requests are made.
"""

import requests

from scripts import watchdog


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Routes /health GETs to a queue, records external pings and POSTs."""

    def __init__(self, get_results=None, post_result=None):
        self.get_results = list(get_results or [])
        self.posts = []
        self.pings = []
        self._post_result = post_result

    def get(self, url, timeout=None):
        if "health" in url:  # the local health endpoint
            result = self.get_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        # Anything else is an external dead-man's-switch ping.
        self.pings.append(url)
        return FakeResponse(200, {"ok": True})

    def post(self, url, json=None, timeout=None):
        self.posts.append(json or {})
        return self._post_result or FakeResponse(200, {"ok": True})


# --------------------------------------------------------------------------
# check_health
# --------------------------------------------------------------------------
def test_check_health_ok():
    session = FakeSession([FakeResponse(200, {"status": "healthy"})])
    code, body = watchdog.check_health("http://x/health", session=session)
    assert code == 200
    assert body == {"status": "healthy"}


def test_check_health_network_error_returns_zero():
    session = FakeSession([requests.RequestException("connection refused")])
    code, body = watchdog.check_health("http://x/health", session=session)
    assert code == 0
    assert body is None


# --------------------------------------------------------------------------
# send_telegram
# --------------------------------------------------------------------------
def test_send_telegram_not_configured():
    assert watchdog.send_telegram("", "", "hi") is False


def test_send_telegram_success():
    session = FakeSession(post_result=FakeResponse(200, {"ok": True}))
    assert watchdog.send_telegram("tok", "chat", "hi", session=session) is True
    assert session.posts[0]["chat_id"] == "chat"


def test_send_telegram_failure_is_swallowed():
    session = FakeSession(post_result=FakeResponse(500, {"ok": False}, text="err"))
    assert watchdog.send_telegram("tok", "chat", "hi", session=session) is False


# --------------------------------------------------------------------------
# run(): two-strikes alerting
# --------------------------------------------------------------------------
def test_run_alerts_after_two_consecutive_failures():
    session = FakeSession(get_results=[FakeResponse(503), FakeResponse(503)])
    code = watchdog.run(
        max_checks=2, sleep=lambda _s: None, session=session, token="tok", chat_id="chat"
    )
    assert code == 1
    assert len(session.posts) == 1
    assert "DOWN" in session.posts[0]["text"]


def test_run_single_failure_does_not_alert():
    session = FakeSession(get_results=[FakeResponse(503), FakeResponse(200)])
    code = watchdog.run(
        max_checks=2, sleep=lambda _s: None, session=session, token="tok", chat_id="chat"
    )
    assert code == 1
    assert session.posts == []


def test_run_all_healthy_does_not_alert():
    session = FakeSession(get_results=[FakeResponse(200), FakeResponse(200)])
    code = watchdog.run(
        max_checks=2, sleep=lambda _s: None, session=session, token="tok", chat_id="chat"
    )
    assert code == 0
    assert session.posts == []


def test_run_sends_email_alert_alongside_telegram(monkeypatch):
    emails = []
    monkeypatch.setattr(
        watchdog, "send_email_alert", lambda s, b: emails.append((s, b)) or True
    )
    session = FakeSession(get_results=[FakeResponse(503), FakeResponse(503)])
    watchdog.run(
        max_checks=2, sleep=lambda _s: None, session=session, token="tok", chat_id="chat"
    )
    assert len(session.posts) == 1  # telegram
    assert len(emails) == 1         # email


def test_run_network_errors_count_as_failures():
    session = FakeSession(
        get_results=[requests.RequestException("x"), requests.RequestException("y")]
    )
    code = watchdog.run(
        max_checks=2, sleep=lambda _s: None, session=session, token="tok", chat_id="chat"
    )
    assert code == 1
    assert len(session.posts) == 1


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def test_parse_args_once_and_interval():
    assert watchdog.parse_args([]).once is False
    assert watchdog.parse_args(["--once"]).once is True
    assert watchdog.parse_args(["--interval", "60"]).interval == 60


def test_parse_args_dry_run():
    assert watchdog.parse_args(["--dry-run"]).dry_run is True


# --------------------------------------------------------------------------
# External dead-man's-switch ping
# --------------------------------------------------------------------------
def test_ping_external_disabled_when_url_empty():
    assert watchdog.ping_external("", session=FakeSession()) is False


def test_run_pings_external_url_on_every_success():
    session = FakeSession(get_results=[FakeResponse(200), FakeResponse(200)])
    code = watchdog.run(
        max_checks=2, sleep=lambda _s: None, session=session,
        ping_url="https://hc-ping.com/abc",
    )
    assert code == 0
    assert session.pings == ["https://hc-ping.com/abc", "https://hc-ping.com/abc"]


def test_run_does_not_ping_external_url_on_failure():
    session = FakeSession(get_results=[FakeResponse(503), FakeResponse(200)])
    watchdog.run(
        max_checks=2, sleep=lambda _s: None, session=session,
        ping_url="https://hc-ping.com/abc",
    )
    # Only the successful (second) check pings.
    assert session.pings == ["https://hc-ping.com/abc"]


def test_parse_args_ping_url():
    assert watchdog.parse_args(["--ping-url", "https://hc-ping.com/x"]).ping_url == "https://hc-ping.com/x"
