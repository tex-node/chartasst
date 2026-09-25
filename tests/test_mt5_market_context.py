from types import SimpleNamespace

from app.mt5_handler import MT5Handler


class FakeMT5:
    TIMEFRAME_H1 = 16385
    TIMEFRAME_D1 = 16408
    TIMEFRAME_W1 = 32769

    def __init__(self):
        self.calls = []

    def terminal_info(self):
        return SimpleNamespace(connected=True)

    def symbol_info(self, symbol):
        return SimpleNamespace(visible=True)

    def symbol_select(self, symbol, enabled):
        return True

    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        self.calls.append((symbol, timeframe, start, count))
        if timeframe == self.TIMEFRAME_H1:
            # MT5 position 0 is the live/forming bar. The handler must exclude it.
            return [
                {"time": 100, "open": 98, "high": 101, "low": 97, "close": 99},
                {"time": 200, "open": 99, "high": 103, "low": 98, "close": 102},
                {"time": 300, "open": 102, "high": 104, "low": 100, "close": 103},
                {"time": 400, "open": 103, "high": 999, "low": 1, "close": 999},
            ]
        if timeframe == self.TIMEFRAME_D1:
            return [
                {"time": 50, "open": 90, "high": 110, "low": 80, "close": 100}
            ]
        if timeframe == self.TIMEFRAME_W1:
            return [
                {"time": 25, "open": 70, "high": 120, "low": 60, "close": 95}
            ]
        return []

    def last_error(self):
        return (0, "ok")


def test_market_context_uses_completed_bar_not_forming_bar():
    fake = FakeMT5()
    handler = MT5Handler(mt5_module=fake, auto_connect=False)
    handler.connected = True

    context = handler.get_market_context("XAUUSD", "H1", count=4)

    assert "error" not in context
    assert context["symbol"] == "XAUUSD"
    assert context["timeframe"] == "H1"

    # The 999 close belongs to the currently forming bar and must never become
    # the market close used by hypothesis evaluation.
    assert context["close"] == 103
    assert context["price"] == 103
    assert context["previous_close"] == 102
    assert context["timestamp"] == "1970-01-01T00:05:00+00:00"

    assert context["references"]["prev_day_high"] == 110
    assert context["references"]["prev_day_low"] == 80
    assert context["references"]["prev_week_high"] == 120
    assert context["references"]["prev_week_low"] == 60

    # Daily/weekly reference requests must explicitly use completed bars
    # (position 1), not the current forming period (position 0).
    assert ("XAUUSD", fake.TIMEFRAME_D1, 1, 1) in fake.calls
    assert ("XAUUSD", fake.TIMEFRAME_W1, 1, 1) in fake.calls
