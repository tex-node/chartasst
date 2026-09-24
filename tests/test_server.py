"""Unit tests for :mod:`app.server`.

The MT5 handler and notifier are replaced with in-memory fakes, so these tests
never touch a terminal or the network.
"""

import pytest

from app.config import Config
from app.server import create_app

API_KEY = "test-secret-key"
HEADERS = {"X-API-Key": API_KEY}

PLAN = {
    "id": "plan_001",
    "name": "EURUSD Resistance Breakout",
    "symbol": "EURUSD",
    "action": "buy",
    "status": "active",
    "object_name": "Resistance_1.0850",
}


class FakeMatcher:
    def __init__(self, plan=PLAN, raise_on_match=False, stale=None):
        self.plan = plan
        self.raise_on_match = raise_on_match
        self.stale = stale if stale is not None else []

    def match(self, signal):
        if self.raise_on_match:
            raise RuntimeError("matcher exploded")
        return self.plan

    def match_by_object(self, object_name):
        return self.plan

    def get_active_plans(self):
        return [self.plan] if self.plan else []

    def get_stale_plans(self, max_age_days=30):
        return self.stale

    def count_stale_plans(self, max_age_days=30):
        return len(self.stale)


class FakeHandler:
    def __init__(self, connected=True, exec_result=None):
        self.connected = connected
        self.exec_result = exec_result if exec_result is not None else {
            "success": True,
            "order": 111,
            "price": 1.0851,
            "sl": 1.0831,
            "tp": 1.0871,
        }
        self.executed = []
        self.closed = []
        self.retcode_counts = {}

    def is_connected(self):
        return self.connected

    def get_retcode_counts(self):
        return dict(self.retcode_counts)

    def execute_plan(self, plan, signal):
        self.executed.append((plan, signal))
        return self.exec_result

    def get_positions(self):
        return {"count": 1, "positions": [{"ticket": 111, "symbol": "EURUSD"}]}

    def close_position(self, ticket):
        self.closed.append(ticket)
        return {"success": True, "order": ticket}

    def shutdown(self):
        pass


class FakeNotifier:
    def __init__(self):
        self.signal_alerts = []
        self.unplanned_alerts = []
        self.startups = []

    def send_signal_alert(self, signal, plan, result):
        self.signal_alerts.append((signal, plan, result))
        return True

    def send_unplanned_alert(self, signal):
        self.unplanned_alerts.append(signal)
        return True

    def send_startup_message(self, mt5_connected, plan_count, live_trading):
        self.startups.append(
            {
                "mt5_connected": mt5_connected,
                "plan_count": plan_count,
                "live_trading": live_trading,
            }
        )
        return True


@pytest.fixture(autouse=True)
def _set_api_key(monkeypatch):
    monkeypatch.setattr(Config, "API_KEY", API_KEY)


@pytest.fixture
def client():
    app = create_app(matcher=FakeMatcher(), handler=FakeHandler(), notifier=FakeNotifier())
    app.testing = True
    return app.test_client()


@pytest.fixture
def parts():
    """Return (matcher, handler, notifier, client) for tests needing the fakes."""
    matcher, handler, notifier = FakeMatcher(), FakeHandler(), FakeNotifier()
    app = create_app(matcher=matcher, handler=handler, notifier=notifier)
    app.testing = True
    return matcher, handler, notifier, app.test_client()


# --------------------------------------------------------------------------
# /health
# --------------------------------------------------------------------------
def test_health_healthy_when_connected():
    app = create_app(matcher=FakeMatcher(), handler=FakeHandler(connected=True), notifier=FakeNotifier())
    app.testing = True
    resp = app.test_client().get("/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "healthy"
    assert body["mt5_connected"] is True


def test_health_degraded_when_disconnected():
    app = create_app(matcher=FakeMatcher(), handler=FakeHandler(connected=False), notifier=FakeNotifier())
    app.testing = True
    resp = app.test_client().get("/health")
    assert resp.status_code == 503
    body = resp.get_json()
    assert body["status"] == "degraded"
    assert body["mt5_connected"] is False


# --------------------------------------------------------------------------
# /health/full
# --------------------------------------------------------------------------
def test_health_full_reports_runtime_state(parts):
    _, handler, _, client = parts
    resp = client.get("/health/full")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["mt5_connected"] is True
    assert body["status"] == "healthy"
    assert body["open_positions"] == 1
    assert body["last_webhook_received"] is None
    assert body["last_webhook_source"] is None
    assert body["uptime_seconds"] >= 0

    # A received webhook must update the tracked timestamp/source/count.
    client.post(
        "/webhook/tradingview",
        json={"symbol": "EURUSD", "action": "buy"},
        headers=HEADERS,
    )
    body2 = client.get("/health/full").get_json()
    assert body2["last_webhook_received"] is not None
    assert body2["last_webhook_source"] == "tradingview"
    assert body2["webhook_count"] == 1


def test_health_full_degraded_when_disconnected():
    app = create_app(
        matcher=FakeMatcher(), handler=FakeHandler(connected=False), notifier=FakeNotifier()
    )
    app.testing = True
    resp = app.test_client().get("/health/full")
    assert resp.status_code == 503
    body = resp.get_json()
    assert body["status"] == "degraded"
    assert body["mt5_connected"] is False


def test_health_full_tracks_mt5_source(parts):
    _, _, _, client = parts
    client.post(
        "/webhook/mt5",
        json={"event": "cross", "object_name": "Resistance_1.0850"},
        headers=HEADERS,
    )
    body = client.get("/health/full").get_json()
    assert body["last_webhook_source"] == "mt5"


def test_health_full_records_unauthenticated_webhooks(parts):
    """A 401 must still update last_webhook_received (tunnel-liveness signal)."""
    _, _, _, client = parts
    client.post("/webhook/tradingview", json={"symbol": "EURUSD", "action": "buy"})  # no key

    body = client.get("/health/full").get_json()
    assert body["last_webhook_received"] is not None
    assert body["webhook_count"] == 1
    assert body["unauthorized_count"] == 1
    # ...but it must NOT count as authorized.
    assert body["authorized_webhook_count"] == 0
    assert body["last_authorized_webhook_received"] is None


def test_health_full_records_malformed_json(parts):
    _, _, _, client = parts
    client.post(
        "/webhook/tradingview",
        data="not json",
        content_type="application/json",
        headers=HEADERS,
    )
    body = client.get("/health/full").get_json()
    assert body["last_webhook_received"] is not None
    assert body["webhook_count"] == 1


def test_health_full_includes_stale_plans():
    matcher = FakeMatcher(stale=[PLAN])
    app = create_app(matcher=matcher, handler=FakeHandler(), notifier=FakeNotifier())
    app.testing = True
    body = app.test_client().get("/health/full").get_json()
    assert body["stale_plans"] == 1
    assert body["stale_plan_ids"] == [PLAN["id"]]


# --------------------------------------------------------------------------
# Startup notification
# --------------------------------------------------------------------------
def test_startup_notification_sent_when_telegram_enabled(monkeypatch):
    monkeypatch.setattr(Config, "TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setattr(Config, "TELEGRAM_CHAT_ID", "chat")
    notifier = FakeNotifier()
    create_app(matcher=FakeMatcher(), handler=FakeHandler(), notifier=notifier)
    assert len(notifier.startups) == 1
    assert notifier.startups[0]["mt5_connected"] is True
    assert notifier.startups[0]["plan_count"] == 1


def test_startup_notification_suppressed_when_telegram_disabled(monkeypatch):
    monkeypatch.setattr(Config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(Config, "TELEGRAM_CHAT_ID", "")
    notifier = FakeNotifier()
    create_app(matcher=FakeMatcher(), handler=FakeHandler(), notifier=notifier)
    assert notifier.startups == []


def test_startup_notification_failure_does_not_break_app(monkeypatch):
    monkeypatch.setattr(Config, "TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setattr(Config, "TELEGRAM_CHAT_ID", "chat")

    class BoomNotifier(FakeNotifier):
        def send_startup_message(self, **kwargs):
            raise RuntimeError("telegram down")

    app = create_app(matcher=FakeMatcher(), handler=FakeHandler(), notifier=BoomNotifier())
    assert app is not None  # construction survived the failure


# --------------------------------------------------------------------------
# API key auth
# --------------------------------------------------------------------------
def test_webhook_requires_api_key(client):
    resp = client.post("/webhook/tradingview", json={"symbol": "EURUSD", "action": "buy"})
    assert resp.status_code == 401


def test_webhook_rejects_wrong_api_key(client):
    resp = client.post(
        "/webhook/tradingview",
        json={"symbol": "EURUSD", "action": "buy"},
        headers={"X-API-Key": "nope"},
    )
    assert resp.status_code == 401


def test_positions_requires_api_key(client):
    assert client.get("/positions").status_code == 401


def test_close_requires_api_key(client):
    assert client.post("/close/111").status_code == 401


# --------------------------------------------------------------------------
# Payload validation
# --------------------------------------------------------------------------
def test_invalid_json_returns_400(client):
    resp = client.post(
        "/webhook/tradingview",
        data="this is not json",
        content_type="application/json",
        headers=HEADERS,
    )
    assert resp.status_code == 400


def test_missing_symbol_returns_400(client):
    resp = client.post("/webhook/tradingview", json={"action": "buy"}, headers=HEADERS)
    assert resp.status_code == 400


def test_mt5_missing_object_returns_400(client):
    resp = client.post("/webhook/mt5", json={"event": "cross"}, headers=HEADERS)
    assert resp.status_code == 400


# --------------------------------------------------------------------------
# TradingView webhook behaviour
# --------------------------------------------------------------------------
def test_tradingview_matched_executes_and_notifies(parts):
    _, handler, notifier, client = parts
    resp = client.post(
        "/webhook/tradingview",
        json={"symbol": "EURUSD", "action": "buy", "price": 1.0851},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "received"
    assert body["matched"] is True
    assert body["executed"] is True
    assert len(handler.executed) == 1
    assert len(notifier.signal_alerts) == 1
    assert len(notifier.unplanned_alerts) == 0


def test_tradingview_unplanned_does_not_execute(parts):
    matcher, handler, notifier, client = parts
    matcher.plan = None  # no matching plan
    resp = client.post(
        "/webhook/tradingview",
        json={"symbol": "EURUSD", "action": "buy", "price": 1.0851},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["matched"] is False
    assert body["executed"] is False
    assert len(handler.executed) == 0
    assert len(notifier.unplanned_alerts) == 1


def test_tradingview_execution_failure_reports_not_executed(parts):
    matcher, handler, notifier, client = parts
    handler.exec_result = {"success": False, "error": "market closed"}
    resp = client.post(
        "/webhook/tradingview",
        json={"symbol": "EURUSD", "action": "buy", "price": 1.0851},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["matched"] is True
    assert body["executed"] is False
    # The notifier is still called with the failure result.
    assert notifier.signal_alerts[0][2]["success"] is False


def test_tradingview_matcher_exception_returns_500(monkeypatch):
    app = create_app(
        matcher=FakeMatcher(raise_on_match=True),
        handler=FakeHandler(),
        notifier=FakeNotifier(),
    )
    app.testing = True
    resp = app.test_client().post(
        "/webhook/tradingview",
        json={"symbol": "EURUSD", "action": "buy"},
        headers=HEADERS,
    )
    assert resp.status_code == 500
    assert resp.get_json()["status"] == "error"


def test_tradingview_text_plain_json_body(parts):
    """TradingView sends text/plain by default; the server must still parse it."""
    _, handler, _, client = parts
    resp = client.post(
        "/webhook/tradingview",
        data='{"symbol": "EURUSD", "action": "buy", "price": 1.0851}',
        content_type="text/plain",
        headers=HEADERS,
    )
    assert resp.status_code == 200
    assert resp.get_json()["matched"] is True
    assert len(handler.executed) == 1


# --------------------------------------------------------------------------
# MT5 webhook behaviour
# --------------------------------------------------------------------------
def test_mt5_webhook_matches_object(parts):
    _, handler, notifier, client = parts
    resp = client.post(
        "/webhook/mt5",
        json={
            "event": "cross",
            "object_name": "Resistance_1.0850",
            "object_type": "horizontal_line",
            "price": 1.0850,
            "direction": "above",
            "symbol": "EURUSD",
            "timeframe": "PERIOD_H1",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["matched"] is True
    assert body["executed"] is True
    assert len(handler.executed) == 1
    assert len(notifier.signal_alerts) == 1


# --------------------------------------------------------------------------
# Positions / close
# --------------------------------------------------------------------------
def test_positions_returns_data(client):
    resp = client.get("/positions", headers=HEADERS)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["count"] == 1
    assert body["positions"][0]["ticket"] == 111


def test_close_position(client):
    resp = client.post("/close/111", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True


# --------------------------------------------------------------------------
# Kill switch: /panic and /resume
# --------------------------------------------------------------------------
def test_panic_requires_api_key(client):
    assert client.post("/panic").status_code == 401


def test_resume_requires_api_key(client):
    assert client.post("/resume").status_code == 401


def test_panic_disables_trading_and_reports_previous(parts):
    _, _, _, client = parts
    before = client.get("/health/full").get_json()
    assert before["live_trading_effective"] is True
    assert before["panic"] is False

    resp = client.post("/panic", headers=HEADERS)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["panic"] is True
    assert body["previous_live_trading_effective"] is True
    assert body["live_trading_effective"] is False

    after = client.get("/health/full").get_json()
    assert after["live_trading_effective"] is False
    assert after["panic"] is True


def test_resume_clears_kill_switch(parts):
    _, _, _, client = parts
    client.post("/panic", headers=HEADERS)
    resp = client.post("/resume", headers=HEADERS)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["panic"] is False
    assert body["previous_live_trading_effective"] is False
    assert body["live_trading_effective"] is True


def test_panic_does_not_reenable_when_env_disabled(parts, monkeypatch):
    from app.config import Config
    monkeypatch.setattr(Config, "LIVE_TRADING", False)
    _, _, _, client = parts
    # .env disables trading; /resume must not override that.
    client.post("/resume", headers=HEADERS)
    body = client.get("/health/full").get_json()
    assert body["live_trading_effective"] is False


# --------------------------------------------------------------------------
# /stats/today
# --------------------------------------------------------------------------
def test_stats_today_requires_api_key(client):
    assert client.get("/stats/today").status_code == 401


def test_stats_today_reports_aggregates(parts):
    matcher, handler, notifier, client = parts
    handler.retcode_counts = {"10016": 3, "10009": 5, "10019": 1}
    matcher.stale = [PLAN]

    # One trigger (matched webhook) and one uptime sample.
    from app import runtime
    runtime.uptime.record(True)
    runtime.uptime.record(False)
    client.post(
        "/webhook/tradingview",
        json={"symbol": "EURUSD", "action": "buy"},
        headers=HEADERS,
    )

    body = client.get("/stats/today", headers=HEADERS).get_json()
    assert body["active_plans"] == 1
    assert body["stale_plans"] == 1
    assert body["plans_triggered_today"] == 1
    assert body["mt5_uptime_percent"] == 50.0
    assert body["retcode_histogram"] == {"10016": 3, "10009": 5, "10019": 1}
    # DONE (10009) is excluded; failures ordered by count desc.
    codes = [item["retcode"] for item in body["top_non_done_retcodes"]]
    assert codes == [10016, 10019]
    assert "Invalid stops" in body["top_non_done_retcodes"][0]["text"]


def test_stats_today_counts_unauthorized(parts):
    _, _, _, client = parts
    client.post("/webhook/tradingview", json={"symbol": "EURUSD", "action": "buy"})
    body = client.get("/stats/today", headers=HEADERS).get_json()
    assert body["unauthorized_count"] == 1
