"""MetaTrader 5 integration.

Wraps the official ``MetaTrader5`` Python package (Windows-only) behind a small
class so the rest of the application never touches the raw API. The import is
performed defensively: on non-Windows machines or when the package is missing,
the handler simply reports ``connected == False`` instead of crashing on import.
This is what allows the server, matcher and notifier test-suite to run on any OS.

Every method logs its steps and converts exceptions into structured error
dictionaries, so callers can always rely on a return value of the documented
shape.
"""

from __future__ import annotations

import logging
import threading

from app import runtime
from app.config import Config
from app.utils import resolve_symbol

logger = logging.getLogger(__name__)

# --- Defensive import of the Windows-only MT5 package ---------------------
_MT5_IMPORT_ERROR: Exception | None = None
try:  # pragma: no cover - exercised implicitly on import
    import MetaTrader5 as mt5  # type: ignore
except Exception as exc:  # ImportError on non-Windows, or DLL load failure
    mt5 = None  # type: ignore
    _MT5_IMPORT_ERROR = exc

# Filling-mode preference order. Some brokers reject IOC, others reject FOK.
FILLING_MODES = ("ORDER_FILLING_IOC", "ORDER_FILLING_FOK", "ORDER_FILLING_RETURN")

# MT5 retcodes that count as a successful order submission.
_SUCCESS_RETCODES = {"TRADE_RETCODE_DONE", "TRADE_RETCODE_PLACED", "TRADE_RETCODE_DONE_PARTIAL"}

# Human-readable explanations for the common MT5 trade-server retcodes. MT5
# comments ("Invalid stops", "Trade disabled") are terse; these help at 2 AM.
# Keys are the numeric ``TRADE_RETCODE_*`` values, which are fixed by MT5.
RETCODE_MESSAGES = {
    10004: "Requote - the price changed before the order was filled. Try again.",
    10006: "Request rejected by the broker.",
    10007: "Request cancelled by you.",
    10008: "Order placed successfully.",
    10009: "Request completed successfully.",
    10010: "Only part of the request was completed.",
    10011: "Request processing error (server-side).",
    10012: "Request timed out.",
    10013: "Invalid request - malformed parameters.",
    10014: "Invalid volume - outside the symbol's min/max/step.",
    10015: "Invalid price.",
    10016: "Invalid stops - SL/TP too close to price or on the wrong side.",
    10017: "Trading is disabled for this account.",
    10018: "Market is closed.",
    10019: "Not enough money / free margin for this volume.",
    10020: "Prices changed.",
    10021: "No quotes available to process the request.",
    10022: "Invalid order expiration.",
    10023: "Order state changed.",
    10024: "Too many requests - slow down.",
    10025: "No changes were made to the order.",
    10026: "Autotrading is disabled by the server.",
    10027: "Autotrading is disabled in the client terminal.",
    10030: "Unsupported filling mode.",
    10031: "No connection to the trade server.",
    10034: "Order or position limit reached.",
    10036: "Position is already closed.",
    10038: "Close volume exceeds the open position volume.",
    10039: "A close order already exists for this position.",
    10041: "Request rejected - order queue processing.",
    10044: "Only real accounts support this feature.",
    # Internal (non-MT5) rejection codes used by the pre-flight risk check.
    90001: "Pre-flight risk check rejected the order (volume / SL / risk limit).",
}

# Internal retcode returned when the pre-flight risk check rejects an order.
RETCODE_PREFLIGHT_REJECT = 90001

# Retcodes that represent a successful completion (excluded from the digest's
# "top failures" histogram).
DONE_RETCODES = (10008, 10009, 10010)


def describe_retcode(retcode) -> str | None:
    """Return a human-readable explanation for an MT5 retcode.

    Accepts a numeric retcode (or a numeric string) and returns a friendly
    sentence, or ``None`` when ``retcode`` is missing/unrecognised.
    """
    if retcode is None:
        return None
    try:
        key = int(retcode)
    except (TypeError, ValueError):
        return None
    return RETCODE_MESSAGES.get(key, f"Unrecognised MT5 error (retcode {key}).")


class MT5Handler:
    """Thin, well-logged wrapper around the MetaTrader5 Python API."""

    def __init__(self, mt5_module=None, auto_connect: bool = True) -> None:
        """Create the handler and (optionally) connect to the MT5 terminal.

        Parameters
        ----------
        mt5_module:
            Optional injected MT5 module. Defaults to the globally imported
            ``MetaTrader5``. Tests inject a fake module here.
        auto_connect:
            When ``True`` (default) :meth:`connect` is called immediately.
        """
        self.mt5 = mt5_module if mt5_module is not None else mt5
        self.connected = False
        self._available = self.mt5 is not None
        # In-memory histogram of every order_send retcode (for /stats/today).
        self._retcode_counts: dict[int, int] = {}
        self._retcode_lock = threading.Lock()
        if not self._available:
            logger.error(
                "MetaTrader5 package is unavailable (%s). Running in "
                "alert-only / degraded mode.",
                _MT5_IMPORT_ERROR,
            )
        if auto_connect:
            self.connect()

    # ------------------------------------------------------------------ #
    # Connection
    # ------------------------------------------------------------------ #
    def connect(self) -> bool:
        """Initialise the MT5 terminal and log in to the account.

        Returns ``True`` on success. On failure it logs a clear, actionable
        error and leaves ``self.connected`` as ``False``.
        """
        if not self._available:
            logger.error("Cannot connect: MetaTrader5 module not available.")
            self.connected = False
            return False

        try:
            kwargs = {}
            if Config.MT5_PATH:
                kwargs["path"] = Config.MT5_PATH
            if Config.MT5_LOGIN:
                kwargs["login"] = int(Config.MT5_LOGIN)
            if Config.MT5_PASSWORD:
                kwargs["password"] = Config.MT5_PASSWORD
            if Config.MT5_SERVER:
                kwargs["server"] = Config.MT5_SERVER

            logger.info(
                "Initialising MT5 (login=%s, server=%s, path=%s)",
                Config.MT5_LOGIN or "<auto>",
                Config.MT5_SERVER or "<auto>",
                Config.MT5_PATH or "<auto>",
            )
            ok = self.mt5.initialize(**kwargs)
            if not ok:
                code, msg = self.mt5.last_error()
                logger.error(
                    "mt5.initialize() failed: [%s] %s. Check credentials, that "
                    "terminal64.exe is running, and that the path is correct.",
                    code,
                    msg,
                )
                self.connected = False
                return False

            info = self.mt5.terminal_info()
            self.connected = bool(info and getattr(info, "connected", False))
            logger.info(
                "MT5 initialised. Version=%s, company=%s, connected=%s",
                getattr(self.mt5, "version", lambda: "?")(),
                getattr(info, "company", "?"),
                self.connected,
            )
            return True
        except Exception as exc:  # never let a bad terminal crash the server
            logger.error("Unexpected error during MT5 initialisation: %s", exc)
            self.connected = False
            return False

    def is_connected(self) -> bool:
        """Re-check the live terminal status via ``terminal_info()``."""
        if not self._available or not self.connected:
            return False
        try:
            info = self.mt5.terminal_info()
            self.connected = bool(info and getattr(info, "connected", False))
        except Exception as exc:
            logger.error("terminal_info() failed: %s", exc)
            self.connected = False
        return self.connected

    def shutdown(self) -> None:
        """Shut down the MT5 terminal connection (safe to call repeatedly)."""
        if not self._available:
            return
        try:
            self.mt5.shutdown()
            logger.info("MT5 connection shut down.")
        except Exception as exc:
            logger.error("Error during MT5 shutdown: %s", exc)
        finally:
            self.connected = False

    # ------------------------------------------------------------------ #
    # Symbol helpers
    # ------------------------------------------------------------------ #
    def _resolve_symbol(self, symbol: str) -> str:
        """Apply the configured broker suffix to a base symbol."""
        return resolve_symbol(symbol, Config.MT5_DEFAULT_SUFFIX)

    def _ensure_symbol(self, symbol: str) -> bool:
        """Make sure ``symbol`` is visible in Market Watch. Returns success."""
        try:
            info = self.mt5.symbol_info(symbol)
            if info is None:
                logger.error(
                    "Symbol %s not found. Check the name and MT5_DEFAULT_SUFFIX.",
                    symbol,
                )
                return False
            if not getattr(info, "visible", True):
                if not self.mt5.symbol_select(symbol, True):
                    logger.error("symbol_select(%s, True) failed.", symbol)
                    return False
            return True
        except Exception as exc:
            logger.error("Error selecting symbol %s: %s", symbol, exc)
            return False

    def get_market_context(self, symbol: str, timeframe: str, count: int = 10) -> dict:
        """Return normalized OHLC market context for hypothesis evaluation."""
        from app.market_adapter import TIMEFRAME_MAP, build_market_context
        if not self._available or not self.is_connected():
            return {"symbol": symbol, "timeframe": timeframe, "error": "MT5 is not connected."}
        tf_name = TIMEFRAME_MAP.get(str(timeframe).strip().upper())
        if not tf_name or not hasattr(self.mt5, tf_name):
            return {"symbol": symbol, "timeframe": timeframe, "error": "Unsupported timeframe."}
        resolved = self._resolve_symbol(symbol)
        if not self._ensure_symbol(resolved):
            return {"symbol": symbol, "timeframe": timeframe, "error": "Symbol unavailable."}
        try:
            tf = getattr(self.mt5, tf_name)
            rows = self.mt5.copy_rates_from_pos(resolved, tf, 0, max(int(count), 3))
            if rows is None:
                return {"symbol": symbol, "timeframe": timeframe, "error": "No market bars returned."}

            def normalize(items):
                return [
                    {"time": row["time"], "open": row["open"], "high": row["high"],
                     "low": row["low"], "close": row["close"]}
                    for row in items
                ]

            recent = normalize(list(rows))
            recent.sort(key=lambda x: x["time"])
            # Pull the two completed daily bars and two completed weekly bars.
            drows = self.mt5.copy_rates_from_pos(resolved, getattr(self.mt5, "TIMEFRAME_D1"), 1, 1) or []
            wrows = self.mt5.copy_rates_from_pos(resolved, getattr(self.mt5, "TIMEFRAME_W1"), 1, 1) or []
            return build_market_context(resolved, timeframe, recent, normalize(list(drows)), normalize(list(wrows)))
        except Exception as exc:
            logger.error("Market context failed for %s %s: %s", symbol, timeframe, exc)
            return {"symbol": symbol, "timeframe": timeframe, "error": str(exc)}

    # ------------------------------------------------------------------ #
    # Safety guard
    # ------------------------------------------------------------------ #
    def _guard_trading_disabled(self) -> dict | None:
        """Return an error result when live trading is disabled, else ``None``.

        This is the single choke point for *every* state-changing order path
        (``execute_plan``, ``close_position`` and any future modify call), so
        that flipping ``Config.LIVE_TRADING`` to ``False`` - or starting the
        server with ``--dry-run`` - reliably blocks all trading.
        """
        if runtime.flags.live_trading_effective():
            return None
        if runtime.flags.is_trading_forced_off():
            msg = (
                "Trading is disabled by the runtime kill switch "
                "(POST /resume or Telegram /resume to re-enable)."
            )
        else:
            msg = "LIVE_TRADING is disabled (alert-only mode); no order placed."
        logger.warning(msg)
        return {"success": False, "error": msg, "dry_run": True}

    # ------------------------------------------------------------------ #
    # Pre-flight risk check
    # ------------------------------------------------------------------ #
    def _preflight_check(self, plan: dict) -> tuple[bool, str]:
        """Sanity-check a plan's size and risk *before* anything is sent.

        Catches the class of fat-finger mistakes that costs real money, e.g. a
        ``volume`` typed as ``10`` instead of ``0.10``.

        Returns ``(ok, reason)``; ``reason`` is an empty string when ``ok``.

        Rejects when any of these hold:

        * ``volume`` > ``Config.MAX_VOLUME`` (default 1.0)
        * ``sl_points`` < ``Config.MIN_SL_POINTS`` (default 50)
        * the loss at the stop (in account currency) > ``MAX_RISK_PER_TRADE``
          (default 50.0). This uses the symbol's tick value/size; if the broker
          does not expose those, the monetary check is skipped (logged).
        """
        plan = plan or {}
        try:
            volume = float(plan.get("volume") or Config.DEFAULT_VOLUME)
            sl_points = int(plan.get("sl_points") or Config.DEFAULT_STOP_LOSS)
        except (TypeError, ValueError):
            return False, "Pre-flight: non-numeric volume or sl_points."

        if volume <= 0:
            return False, f"Pre-flight: volume must be > 0 (got {volume})."
        if volume > Config.MAX_VOLUME:
            return (
                False,
                f"Pre-flight: volume {volume} exceeds MAX_VOLUME {Config.MAX_VOLUME}.",
            )
        if sl_points < Config.MIN_SL_POINTS:
            return (
                False,
                f"Pre-flight: SL {sl_points} pts is below MIN_SL_POINTS {Config.MIN_SL_POINTS}.",
            )

        # Monetary risk at the stop, in account currency (best effort).
        try:
            symbol = self._resolve_symbol(str(plan.get("symbol", "")))
            info = self.mt5.symbol_info(symbol) if symbol else None
            point = float(getattr(info, "point", 0) or 0)
            tick_size = float(getattr(info, "trade_tick_size", 0) or 0)
            tick_value = float(getattr(info, "trade_tick_value", 0) or 0)
            if point > 0 and tick_size > 0 and tick_value > 0:
                risk = volume * (sl_points * point) / tick_size * tick_value
                if risk > Config.MAX_RISK_PER_TRADE:
                    return (
                        False,
                        f"Pre-flight: risk {risk:.2f} exceeds "
                        f"MAX_RISK_PER_TRADE {Config.MAX_RISK_PER_TRADE}.",
                    )
            else:
                logger.debug(
                    "Pre-flight monetary risk skipped for %s (missing tick value/size).",
                    symbol,
                )
        except Exception as exc:
            logger.warning("Pre-flight monetary risk could not be computed: %s", exc)

        return True, ""

    # ------------------------------------------------------------------ #
    # Retcode histogram
    # ------------------------------------------------------------------ #
    def _record_retcode(self, retcode) -> None:
        """Increment the in-memory retcode histogram (thread-safe)."""
        if retcode is None:
            return
        try:
            key = int(retcode)
        except (TypeError, ValueError):
            return
        with self._retcode_lock:
            self._retcode_counts[key] = self._retcode_counts.get(key, 0) + 1

    def get_retcode_counts(self) -> dict[str, int]:
        """Return a copy of the retcode histogram as ``{"10009": 3, ...}``."""
        with self._retcode_lock:
            return {str(k): v for k, v in self._retcode_counts.items()}

    def reset_retcode_counts(self) -> None:
        """Clear the retcode histogram."""
        with self._retcode_lock:
            self._retcode_counts = {}

    # ------------------------------------------------------------------ #
    # Order execution
    # ------------------------------------------------------------------ #
    def execute_plan(self, plan: dict, signal: dict) -> dict:
        """Place a market order for ``plan`` triggered by ``signal``.

        Returns
        -------
        dict
            ``{"success": True, "order": int, "price": float, "sl": float,
            "tp": float}`` on success, or ``{"success": False, "error": str}``.
        """
        plan = plan or {}
        signal = signal or {}

        guard = self._guard_trading_disabled()
        if guard is not None:
            return guard

        if not self._available or not self.is_connected():
            msg = "MT5 is not connected; cannot execute order."
            logger.error(msg)
            return {"success": False, "error": msg}

        action = str(plan.get("action", "")).strip().lower()
        if action not in ("buy", "sell"):
            msg = f"Invalid plan action: {action!r}"
            logger.error(msg)
            return {"success": False, "error": msg}

        symbol = self._resolve_symbol(str(plan.get("symbol", "")))
        if not symbol:
            return {"success": False, "error": "Plan has no symbol."}

        if not self._ensure_symbol(symbol):
            return {"success": False, "error": f"Symbol {symbol} not available."}

        # Pre-flight risk check runs before anything is sent to the broker.
        ok, reason = self._preflight_check(plan)
        if not ok:
            logger.error(
                "Pre-flight risk check rejected plan %s: %s", plan.get("id"), reason
            )
            return {
                "success": False,
                "error": reason,
                "retcode": RETCODE_PREFLIGHT_REJECT,
                "retcode_text": describe_retcode(RETCODE_PREFLIGHT_REJECT),
            }

        try:
            info = self.mt5.symbol_info(symbol)
            tick = self.mt5.symbol_info_tick(symbol)
            if info is None or tick is None:
                return {"success": False, "error": f"No tick data for {symbol}."}

            point = float(getattr(info, "point", 0.0))
            digits = int(getattr(info, "digits", 5))
            if point <= 0:
                return {"success": False, "error": f"Invalid point size for {symbol}."}

            volume = plan.get("volume") or Config.DEFAULT_VOLUME
            volume = float(volume)

            sl_points = plan.get("sl_points") or Config.DEFAULT_STOP_LOSS
            tp_points = plan.get("tp_points") or Config.DEFAULT_TAKE_PROFIT
            sl_points = int(sl_points)
            tp_points = int(tp_points)

            order_type = self.mt5.ORDER_TYPE_BUY if action == "buy" else self.mt5.ORDER_TYPE_SELL
            price = float(tick.ask if action == "buy" else tick.bid)

            if action == "buy":
                sl = price - sl_points * point
                tp = price + tp_points * point
            else:
                sl = price + sl_points * point
                tp = price - tp_points * point

            sl = round(sl, digits)
            tp = round(tp, digits)
            price = round(price, digits)

            request = {
                "action": self.mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": volume,
                "type": order_type,
                "price": price,
                "sl": sl,
                "tp": tp,
                "deviation": 20,
                "magic": 20260101,
                "comment": f"TA-{plan.get('id', 'plan')}",
                "type_time": self.mt5.ORDER_TIME_GTC,
                "type_filling": self.mt5.ORDER_FILLING_IOC,
            }

            logger.info(
                "Building order for plan %s: %s %s %.2f lots @ %.5f (SL %.5f / TP %.5f)",
                plan.get("id"),
                action.upper(),
                symbol,
                volume,
                price,
                sl,
                tp,
            )

            # Every order goes through the single choke point.
            send = self._send_order(request)
            if not send.get("success"):
                return send
            return {
                "success": True,
                "order": send.get("order"),
                "price": send.get("price", price),
                "sl": sl,
                "tp": tp,
            }
        except Exception as exc:
            logger.error("Unexpected error executing plan %s: %s", plan.get("id"), exc)
            return {"success": False, "error": str(exc)}

    def _send_order(self, request: dict) -> dict:
        """Single choke point for every order sent to MT5.

        ALL order-sending code paths (``execute_plan``, ``close_position`` and
        any future modify / partial-close / close-all / add-order call) MUST go
        through this method. It:

        (a) refuses to send when live trading is disabled,
        (b) runs ``mt5.order_check()`` first for pre-trade validation,
        (c) logs the full request and every result at INFO level,
        (d) retries with alternative filling modes when the broker rejects IOC.

        Returns
        -------
        dict
            On success: ``{"success": True, "order", "price", "retcode",
            "retcode_text", "result"}``. On failure: ``{"success": False,
            "error", "retcode", "retcode_text"}``.
        """
        # (a) Hard safety guard - the last line of defence before order_send.
        guard = self._guard_trading_disabled()
        if guard is not None:
            return guard

        if not self._available or not self.is_connected():
            return {"success": False, "error": "MT5 is not connected; cannot send order."}

        # (b) Pre-trade validation. order_check returns margin/validity info;
        #     a non-zero retcode is informational, not necessarily fatal.
        try:
            check = self.mt5.order_check(request)
            if check is not None:
                logger.info(
                    "order_check: retcode=%s comment=%s",
                    getattr(check, "retcode", "?"),
                    getattr(check, "comment", ""),
                )
        except Exception as exc:
            logger.warning("order_check raised (continuing): %s", exc)

        # (c) Log the full request once, before any send.
        logger.info("order_send request: %s", request)

        last_error = "unknown"
        last_retcode = None
        for mode_name in FILLING_MODES:
            mode = getattr(self.mt5, mode_name, None)
            if mode is None:
                continue
            attempt = dict(request)
            attempt["type_filling"] = mode
            # --- THE single order_send call site in the whole codebase. ---
            result = self.mt5.order_send(attempt)
            if result is None:
                code, msg = self.mt5.last_error()
                last_error = f"order_send returned None [{code}] {msg}"
                logger.error("%s (filling=%s)", last_error, mode_name)
                continue
            retcode = getattr(result, "retcode", None)
            last_retcode = retcode
            self._record_retcode(retcode)
            logger.info(
                "order_send result: retcode=%s comment=%s order=%s price=%s (filling=%s)",
                retcode,
                getattr(result, "comment", ""),
                getattr(result, "order", None),
                getattr(result, "price", None),
                mode_name,
            )
            if self._is_success_retcode(retcode):
                return {
                    "success": True,
                    "order": getattr(result, "order", None),
                    "price": float(getattr(result, "price", 0) or 0),
                    "retcode": retcode,
                    "retcode_text": describe_retcode(retcode),
                    "result": result,
                }
            # Unsupported filling mode is the one retcode worth retrying.
            last_error = (
                f"retcode={retcode} {getattr(result, 'comment', '')} "
                f"(filling={mode_name})"
            )
            if retcode != getattr(self.mt5, "TRADE_RETCODE_INVALID_FILL", None):
                # A non-filling error won't be fixed by changing the mode.
                logger.error("Order rejected: %s", last_error)
                break
            logger.warning("Filling mode %s rejected; trying next.", mode_name)

        return {
            "success": False,
            "error": last_error,
            "retcode": last_retcode,
            "retcode_text": describe_retcode(last_retcode),
        }

    def _is_success_retcode(self, retcode) -> bool:
        """Return True when ``retcode`` corresponds to an accepted order."""
        for name in _SUCCESS_RETCODES:
            if retcode == getattr(self.mt5, name, None):
                return True
        return False

    # ------------------------------------------------------------------ #
    # Positions
    # ------------------------------------------------------------------ #
    @staticmethod
    def _position_to_dict(pos) -> dict:
        """Convert an MT5 position namedtuple into a plain JSON-safe dict."""
        return {
            "ticket": getattr(pos, "ticket", None),
            "symbol": getattr(pos, "symbol", None),
            "type": "buy" if getattr(pos, "type", None) == 0 else "sell",
            "volume": getattr(pos, "volume", None),
            "price_open": getattr(pos, "price_open", None),
            "sl": getattr(pos, "sl", None),
            "tp": getattr(pos, "tp", None),
            "price_current": getattr(pos, "price_current", None),
            "profit": getattr(pos, "profit", None),
            "comment": getattr(pos, "comment", None),
            "time": getattr(pos, "time", None),
        }

    def get_positions(self) -> dict:
        """Return all open positions as ``{"count": int, "positions": [...]}``."""
        if not self._available or not self.is_connected():
            return {"count": 0, "positions": [], "error": "MT5 not connected"}
        try:
            positions = self.mt5.positions_get()
            if positions is None:
                code, msg = self.mt5.last_error()
                logger.error("positions_get() failed: [%s] %s", code, msg)
                return {"count": 0, "positions": [], "error": msg}
            items = [self._position_to_dict(p) for p in positions]
            return {"count": len(items), "positions": items}
        except Exception as exc:
            logger.error("Error retrieving positions: %s", exc)
            return {"count": 0, "positions": [], "error": str(exc)}

    def close_position(self, ticket: int) -> dict:
        """Close the open position identified by ``ticket``.

        Returns ``{"success": bool, ...}``. Blocked entirely (no ``order_send``
        call) when live trading is disabled.
        """
        guard = self._guard_trading_disabled()
        if guard is not None:
            return guard

        if not self._available or not self.is_connected():
            return {"success": False, "error": "MT5 not connected"}

        try:
            positions = self.mt5.positions_get(ticket=ticket)
            if not positions:
                return {"success": False, "error": f"Position {ticket} not found."}

            pos = positions[0]
            symbol = getattr(pos, "symbol", None)
            volume = float(getattr(pos, "volume", 0.0))
            pos_type = getattr(pos, "type", None)

            if self.mt5.ORDER_TYPE_BUY == pos_type:
                close_type = self.mt5.ORDER_TYPE_SELL
            else:
                close_type = self.mt5.ORDER_TYPE_BUY

            tick = self.mt5.symbol_info_tick(symbol)
            if tick is None:
                return {"success": False, "error": f"No tick data for {symbol}."}
            price = float(tick.bid if close_type == self.mt5.ORDER_TYPE_SELL else tick.ask)

            request = {
                "action": self.mt5.TRADE_ACTION_DEAL,
                "position": ticket,
                "symbol": symbol,
                "volume": volume,
                "type": close_type,
                "price": price,
                "deviation": 20,
                "magic": 20260101,
                "comment": "TA-close",
                "type_time": self.mt5.ORDER_TIME_GTC,
                "type_filling": self.mt5.ORDER_FILLING_IOC,
            }

            # Every order goes through the single choke point.
            send = self._send_order(request)
            if not send.get("success"):
                logger.error("Failed to close position %s: %s", ticket, send.get("error"))
                return send
            logger.info("Position %s closed.", ticket)
            return {
                "success": True,
                "order": send.get("order"),
                "price": send.get("price", price),
            }
        except Exception as exc:
            logger.error("Error closing position %s: %s", ticket, exc)
            return {"success": False, "error": str(exc)}
