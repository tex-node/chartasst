"""Unit tests for :mod:`app.mt5_handler`.

The MetaTrader5 module is replaced with an in-memory fake, so no terminal,
account or network is required.
"""

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import mt5_handler, runtime
from app.config import Config
from app.mt5_handler import RETCODE_PREFLIGHT_REJECT, MT5Handler, describe_retcode


class FakeMT5:
    """Minimal fake of the MetaTrader5 module for order-flow testing."""

    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 2
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_FOK = 2
    ORDER_FILLING_RETURN = 3
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_PLACED = 10008
    TRADE_RETCODE_DONE_PARTIAL = 10010
    TRADE_RETCODE_INVALID_FILL = 10030

    def __init__(self, connected=True, point=0.00001, digits=5,
                 tick_value=1.0, tick_size=0.00001):
        self.connected = connected
        self.point = point
        self.digits = digits
        self.tick_value = tick_value
        self.tick_size = tick_size
        self.sent = []
        self.send_results = []
        self.positions = []
        self.selected = []

    # --- lifecycle ---
    def initialize(self, **kwargs):
        return True

    def shutdown(self):
        pass

    def last_error(self):
        return (0, "ok")

    def terminal_info(self):
        return SimpleNamespace(connected=self.connected, company="Fake Ltd")

    def version(self):
        return (500, 0, "fake")

    # --- symbols ---
    def symbol_info(self, symbol):
        return SimpleNamespace(
            point=self.point,
            digits=self.digits,
            visible=True,
            trade_tick_value=self.tick_value,
            trade_tick_size=self.tick_size,
        )

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(bid=1.08500, ask=1.08510)

    def symbol_select(self, symbol, enable):
        self.selected.append((symbol, enable))
        return True

    # --- orders ---
    def order_check(self, request):
        return SimpleNamespace(retcode=0, comment="ok")

    def order_send(self, request):
        self.sent.append(dict(request))
        if self.send_results:
            return self.send_results.pop(0)
        return SimpleNamespace(
            retcode=self.TRADE_RETCODE_DONE,
            order=111,
            price=request.get("price"),
            comment="done",
        )

    def positions_get(self, **kwargs):
        if "ticket" in kwargs:
            return [p for p in self.positions if p.ticket == kwargs["ticket"]]
        return list(self.positions)


def make_position(ticket=111, symbol="EURUSD", ptype=0, volume=0.1):
    return SimpleNamespace(
        ticket=ticket, symbol=symbol, type=ptype, volume=volume,
        price_open=1.0850, sl=1.0800, tp=1.0900, price_current=1.0851,
        profit=1.0, comment="", time=0,
    )


@pytest.fixture
def fake():
    return FakeMT5()


# --------------------------------------------------------------------------
# LIVE_TRADING=false must block EVERY order path
# --------------------------------------------------------------------------
def test_execute_plan_blocked_when_live_trading_disabled(monkeypatch, fake):
    monkeypatch.setattr(Config, "LIVE_TRADING", False)
    handler = MT5Handler(mt5_module=fake, auto_connect=True)

    result = handler.execute_plan(
        {"id": "plan_001", "symbol": "EURUSD", "action": "buy"},
        {"price": 1.0851},
    )

    assert result["success"] is False
    assert result["dry_run"] is True
    assert fake.sent == []  # no order was ever sent


def test_close_position_blocked_when_live_trading_disabled(monkeypatch, fake):
    fake.positions = [make_position(ticket=111)]
    monkeypatch.setattr(Config, "LIVE_TRADING", False)
    handler = MT5Handler(mt5_module=fake, auto_connect=True)

    result = handler.close_position(111)

    assert result["success"] is False
    assert result["dry_run"] is True
    assert fake.sent == []  # no close order was ever sent


def test_guard_returns_none_when_live_trading_enabled(monkeypatch, fake):
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    handler = MT5Handler(mt5_module=fake, auto_connect=True)
    assert handler._guard_trading_disabled() is None


# --------------------------------------------------------------------------
# Connection
# --------------------------------------------------------------------------
def test_connect_success(fake):
    handler = MT5Handler(mt5_module=fake, auto_connect=True)
    assert handler.is_connected() is True


def test_disconnected_handler_reports_not_connected():
    handler = MT5Handler(mt5_module=FakeMT5(connected=False), auto_connect=True)
    assert handler.is_connected() is False


def test_execute_plan_when_disconnected_returns_error(monkeypatch):
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    handler = MT5Handler(mt5_module=FakeMT5(connected=False), auto_connect=True)
    result = handler.execute_plan({"symbol": "EURUSD", "action": "buy"}, {})
    assert result["success"] is False
    assert "not connected" in result["error"].lower()


# --------------------------------------------------------------------------
# Order construction
# --------------------------------------------------------------------------
def test_execute_plan_buy_computes_sl_tp(monkeypatch, fake):
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    handler = MT5Handler(mt5_module=fake, auto_connect=True)

    result = handler.execute_plan(
        {"id": "plan_001", "symbol": "EURUSD", "action": "buy",
         "volume": 0.1, "sl_points": 200, "tp_points": 400},
        {"price": 1.0851},
    )

    assert result["success"] is True
    assert result["order"] == 111
    request = fake.sent[-1]
    assert request["type"] == fake.ORDER_TYPE_BUY
    assert request["volume"] == 0.1
    # Buy: SL below ask, TP above ask (point = 0.00001).
    assert request["sl"] == pytest.approx(1.08510 - 200 * 0.00001)
    assert request["tp"] == pytest.approx(1.08510 + 400 * 0.00001)


def test_execute_plan_sell_computes_sl_tp(monkeypatch, fake):
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    handler = MT5Handler(mt5_module=fake, auto_connect=True)

    handler.execute_plan(
        {"id": "plan_002", "symbol": "EURUSD", "action": "sell",
         "volume": 0.05, "sl_points": 100, "tp_points": 200},
        {"price": 1.0850},
    )

    request = fake.sent[-1]
    assert request["type"] == fake.ORDER_TYPE_SELL
    # Sell: SL above bid, TP below bid.
    assert request["sl"] == pytest.approx(1.08500 + 100 * 0.00001)
    assert request["tp"] == pytest.approx(1.08500 - 200 * 0.00001)


def test_execute_plan_applies_broker_suffix(monkeypatch, fake):
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    monkeypatch.setattr(Config, "MT5_DEFAULT_SUFFIX", ".r")
    handler = MT5Handler(mt5_module=fake, auto_connect=True)

    handler.execute_plan({"symbol": "EURUSD", "action": "buy"}, {})

    assert fake.sent[-1]["symbol"] == "EURUSD.r"


def test_execute_plan_invalid_action_rejected(monkeypatch, fake):
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    handler = MT5Handler(mt5_module=fake, auto_connect=True)

    result = handler.execute_plan({"symbol": "EURUSD", "action": "hold"}, {})
    assert result["success"] is False
    assert fake.sent == []


def test_execute_plan_uses_defaults_when_plan_omits_values(monkeypatch, fake):
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    monkeypatch.setattr(Config, "DEFAULT_VOLUME", 0.02)
    monkeypatch.setattr(Config, "DEFAULT_STOP_LOSS", 150)
    monkeypatch.setattr(Config, "DEFAULT_TAKE_PROFIT", 300)
    handler = MT5Handler(mt5_module=fake, auto_connect=True)

    handler.execute_plan({"symbol": "EURUSD", "action": "buy"}, {})

    request = fake.sent[-1]
    assert request["volume"] == 0.02
    assert request["sl"] == pytest.approx(1.08510 - 150 * 0.00001)
    assert request["tp"] == pytest.approx(1.08510 + 300 * 0.00001)


def test_filling_mode_falls_back_when_ioc_rejected(monkeypatch, fake):
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    # First (IOC) attempt fails with an invalid-fill retcode; second (FOK) works.
    fake.send_results = [
        SimpleNamespace(retcode=fake.TRADE_RETCODE_INVALID_FILL, order=0, price=0, comment="invalid fill"),
        SimpleNamespace(retcode=fake.TRADE_RETCODE_DONE, order=222, price=1.0851, comment="done"),
    ]
    handler = MT5Handler(mt5_module=fake, auto_connect=True)

    result = handler.execute_plan({"symbol": "EURUSD", "action": "buy"}, {})

    assert result["success"] is True
    assert result["order"] == 222
    assert len(fake.sent) == 2
    assert fake.sent[0]["type_filling"] == fake.ORDER_FILLING_IOC
    assert fake.sent[1]["type_filling"] == fake.ORDER_FILLING_FOK


# --------------------------------------------------------------------------
# Positions / close
# --------------------------------------------------------------------------
def test_get_positions(fake):
    fake.positions = [make_position(ticket=1), make_position(ticket=2)]
    handler = MT5Handler(mt5_module=fake, auto_connect=True)
    result = handler.get_positions()
    assert result["count"] == 2


def test_close_position_success(monkeypatch, fake):
    fake.positions = [make_position(ticket=111, symbol="EURUSD", ptype=0, volume=0.1)]
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    handler = MT5Handler(mt5_module=fake, auto_connect=True)

    result = handler.close_position(111)

    assert result["success"] is True
    request = fake.sent[-1]
    # Closing a BUY position means sending a SELL order for the same volume.
    assert request["type"] == fake.ORDER_TYPE_SELL
    assert request["position"] == 111
    assert request["volume"] == 0.1


def test_close_position_not_found(monkeypatch, fake):
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    handler = MT5Handler(mt5_module=fake, auto_connect=True)
    result = handler.close_position(999)
    assert result["success"] is False
    assert fake.sent == []


# --------------------------------------------------------------------------
# Single order-send choke point
# --------------------------------------------------------------------------
def test_order_send_called_only_from_send_order_method():
    """Static guard: no ``order_send`` may appear outside ``_send_order``.

    If someone adds a new order path (modify SL/TP, partial close, close-all)
    and calls ``mt5.order_send`` directly, this test fails - forcing them to
    route it through the guarded ``_send_order`` choke point.
    """
    source_path = Path(mt5_handler.__file__)
    lines = source_path.read_text(encoding="utf-8").splitlines()

    call_lines = [
        (i, line) for i, line in enumerate(lines)
        if ".order_send(" in line and not line.strip().startswith("#")
    ]
    # Exactly one real call site, and it lives in _send_order.
    assert len(call_lines) == 1, f"Expected 1 order_send call site, found {len(call_lines)}: {call_lines}"

    call_index = call_lines[0][0]
    enclosing = None
    for i in range(call_index, -1, -1):
        stripped = lines[i].lstrip()
        if stripped.startswith("def "):
            enclosing = stripped[4:].split("(")[0].strip()
            break
    assert enclosing == "_send_order", f"order_send is called from {enclosing!r}, not '_send_order'"


def test_send_order_is_the_only_send_entrypoint():
    """execute_plan and close_position must delegate to _send_order."""
    # Both public order paths exist and _send_order is a method with the guard.
    assert hasattr(MT5Handler, "_send_order")
    source = inspect.getsource(MT5Handler.execute_plan)
    assert "_send_order(" in source
    source_close = inspect.getsource(MT5Handler.close_position)
    assert "_send_order(" in source_close


# --------------------------------------------------------------------------
# Retcode mapping
# --------------------------------------------------------------------------
def test_describe_retcode_known_value():
    assert "Invalid stops" in describe_retcode(10016)
    assert "Not enough money" in describe_retcode(10019)
    assert "Unsupported filling mode" in describe_retcode(10030)


def test_describe_retcode_unknown_and_none():
    assert describe_retcode(None) is None
    assert "Unrecognised" in describe_retcode(99999)
    assert describe_retcode("not-a-number") is None


# --------------------------------------------------------------------------
# Pre-flight risk check
# --------------------------------------------------------------------------
def test_preflight_returns_tuple(monkeypatch, fake):
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    handler = MT5Handler(mt5_module=fake, auto_connect=True)
    ok, reason = handler._preflight_check({"symbol": "EURUSD", "action": "buy",
                                           "volume": 0.01, "sl_points": 200})
    assert ok is True
    assert reason == ""


def test_preflight_rejects_oversized_volume(monkeypatch, fake):
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    monkeypatch.setattr(Config, "MAX_VOLUME", 1.0)
    handler = MT5Handler(mt5_module=fake, auto_connect=True)

    result = handler.execute_plan(
        {"id": "fat_finger", "symbol": "EURUSD", "action": "buy",
         "volume": 10.0, "sl_points": 200},
        {},
    )

    assert result["success"] is False
    assert result["retcode"] == RETCODE_PREFLIGHT_REJECT
    assert "MAX_VOLUME" in result["error"]
    assert fake.sent == []  # never reached order_send


def test_preflight_rejects_tight_stop(monkeypatch, fake):
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    monkeypatch.setattr(Config, "MIN_SL_POINTS", 50)
    handler = MT5Handler(mt5_module=fake, auto_connect=True)

    result = handler.execute_plan(
        {"symbol": "EURUSD", "action": "buy", "volume": 0.01, "sl_points": 10},
        {},
    )
    assert result["success"] is False
    assert "MIN_SL_POINTS" in result["error"]
    assert fake.sent == []


def test_preflight_rejects_excess_risk(monkeypatch, fake):
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    monkeypatch.setattr(Config, "MAX_RISK_PER_TRADE", 50.0)
    # volume 0.5 * 200 points * 1.0 currency/point = 100 > 50
    handler = MT5Handler(mt5_module=fake, auto_connect=True)

    result = handler.execute_plan(
        {"symbol": "EURUSD", "action": "buy", "volume": 0.5, "sl_points": 200},
        {},
    )
    assert result["success"] is False
    assert "MAX_RISK_PER_TRADE" in result["error"]
    assert fake.sent == []


def test_preflight_accepts_within_limits(monkeypatch, fake):
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    handler = MT5Handler(mt5_module=fake, auto_connect=True)
    result = handler.execute_plan(
        {"symbol": "EURUSD", "action": "buy", "volume": 0.01, "sl_points": 200},
        {},
    )
    assert result["success"] is True
    assert len(fake.sent) == 1


def test_preflight_retcode_is_mapped():
    assert "Pre-flight" in describe_retcode(RETCODE_PREFLIGHT_REJECT)


# --------------------------------------------------------------------------
# Retcode histogram
# --------------------------------------------------------------------------
def test_retcode_histogram_counts_success(monkeypatch, fake):
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    handler = MT5Handler(mt5_module=fake, auto_connect=True)
    handler.execute_plan({"symbol": "EURUSD", "action": "buy"}, {})
    assert handler.get_retcode_counts() == {"10009": 1}


def test_retcode_histogram_counts_failures(monkeypatch, fake):
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    fake.send_results = [
        SimpleNamespace(retcode=10016, order=0, price=0, comment="Invalid stops"),
    ]
    handler = MT5Handler(mt5_module=fake, auto_connect=True)
    handler.execute_plan({"symbol": "EURUSD", "action": "buy"}, {})
    assert handler.get_retcode_counts() == {"10016": 1}


def test_retcode_histogram_reset(monkeypatch, fake):
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    handler = MT5Handler(mt5_module=fake, auto_connect=True)
    handler.execute_plan({"symbol": "EURUSD", "action": "buy"}, {})
    handler.reset_retcode_counts()
    assert handler.get_retcode_counts() == {}


# --------------------------------------------------------------------------
# Kill switch enforcement
# --------------------------------------------------------------------------
def test_execute_blocked_when_panic_engaged(monkeypatch, fake):
    monkeypatch.setattr(Config, "LIVE_TRADING", True)  # .env allows trading
    runtime.flags.force_disable_trading("test")        # runtime override wins
    handler = MT5Handler(mt5_module=fake, auto_connect=True)

    result = handler.execute_plan({"symbol": "EURUSD", "action": "buy"}, {})

    assert result["success"] is False
    assert result["dry_run"] is True
    assert fake.sent == []


def test_close_blocked_when_panic_engaged(monkeypatch, fake):
    fake.positions = [make_position(ticket=111)]
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    runtime.flags.force_disable_trading("test")
    handler = MT5Handler(mt5_module=fake, auto_connect=True)

    result = handler.close_position(111)
    assert result["success"] is False
    assert fake.sent == []


def test_send_order_surfaces_retcode_text(monkeypatch, fake):
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    # Reject with INVALID_STOPS on every filling mode (not a filling error, so
    # it breaks after the first attempt).
    fake.send_results = [
        SimpleNamespace(retcode=10016, order=0, price=0, comment="Invalid stops"),
    ]
    handler = MT5Handler(mt5_module=fake, auto_connect=True)

    result = handler.execute_plan({"symbol": "EURUSD", "action": "buy"}, {})

    assert result["success"] is False
    assert result["retcode"] == 10016
    assert "Invalid stops" in result["retcode_text"]
