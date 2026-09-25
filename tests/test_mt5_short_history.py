"""PHASE 5 - the forming candle must never be treated as completed, regardless
of how short the MT5 history is."""

from types import SimpleNamespace

from app.mt5_handler import MT5Handler


class FakeMT5:
    TIMEFRAME_H1 = 16385
    TIMEFRAME_D1 = 16408
    TIMEFRAME_W1 = 32769

    def __init__(self, h1_rows):
        self.h1_rows = h1_rows

    def terminal_info(self):
        return SimpleNamespace(connected=True)

    def symbol_info(self, symbol):
        return SimpleNamespace(visible=True, point=0.00001, digits=5)

    def symbol_select(self, symbol, enabled):
        return True

    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        if timeframe == self.TIMEFRAME_H1:
            return list(self.h1_rows)
        # No daily/weekly references in these focused tests.
        return []

    def last_error(self):
        return (0, "ok")


def bar(time, close):
    """A single OHLC row; high/low are irrelevant to these assertions."""
    return {"time": time, "open": close, "high": close + 1, "low": close - 1, "close": close}


# The forming candle is always the newest MT5 position-0 bar; give it a
# unmistakable sentinel close so we can detect leakage.
FORMING = bar(300, 999)


def context(rows):
    handler = MT5Handler(mt5_module=FakeMT5(rows), auto_connect=False)
    handler.connected = True
    return handler.get_market_context("XAUUSD", "H1")


def test_zero_bars_returns_error():
    result = context([])
    assert "error" in result
    assert result.get("close") is None


def test_single_forming_bar_returns_error():
    # The only row is position 0 (the forming candle); there is no completed bar.
    result = context([FORMING])
    assert "error" in result
    assert result.get("close") is None


def test_two_bars_excludes_the_forming_candle():
    result = context([bar(200, 103), FORMING])
    assert "error" not in result
    assert result["close"] == 103          # completed bar, never the 999 forming
    assert result["previous_close"] is None  # only one completed bar available
    assert result["event"] == "bar_close"


def test_three_bars_excludes_the_forming_candle():
    result = context([bar(100, 99), bar(200, 103), FORMING])
    assert "error" not in result
    assert result["close"] == 103
    assert result["previous_close"] == 99


def test_four_bars_excludes_the_forming_candle():
    result = context([bar(100, 98), bar(200, 101), bar(300, 103), FORMING])
    assert "error" not in result
    assert result["close"] == 103
    assert result["previous_close"] == 101
