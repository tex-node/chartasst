"""Flask webhook + REST API server.

Exposes:

==============================       ===========================================
Endpoint                             Purpose
==============================       ===========================================
``GET  /health``                     liveness + MT5 connection status
``POST /webhook/tradingview``        receive TradingView alert webhooks
``POST /webhook/mt5``                receive MQL5 EA chart-object webhooks
``GET  /positions``                  list open MT5 positions
``POST /close/<ticket>``             close an MT5 position
==============================       ===========================================

Every ``/webhook`` and trading endpoint requires the ``X-API-Key`` header to
match ``Config.API_KEY``. All handlers log the full payload, tolerate malformed
input, and convert unexpected exceptions into a JSON 500 rather than crashing.

The app is built by :func:`create_app`, which accepts optional ``matcher``,
``handler`` and ``notifier`` objects so tests can inject fakes.
"""

from __future__ import annotations

import logging
import threading
import time

from flask import Flask, jsonify, request

from app.gui import register_gui
from app.hypothesis_state import normalize_status, record_event, transition

from app import runtime
from app.config import Config
from app.mt5_handler import MT5Handler, describe_retcode
from app.notifier import Notifier
from app.plan_matcher import PlanMatcher
from app.utils import utc_date_str, utc_now_iso

logger = logging.getLogger(__name__)

# Duplicate-signal detection window (seconds). Duplicates are *logged* but
# still processed, to honour the "tolerate duplicates" requirement.
DUPLICATE_WINDOW_SEC = 10


def _json_error(message: str, code: int):
    """Build a consistent JSON error response."""
    return jsonify({"status": "error", "error": message}), code


def start_uptime_sampler(app: Flask, interval: float = 60.0, stop_event=None):
    """Start a daemon thread that samples MT5 connectivity for uptime stats.

    Samples feed :data:`app.runtime.uptime`, which ``/stats/today`` reads to
    report an MT5 uptime percentage since midnight UTC. Returns the thread.
    """
    handler = getattr(app, "mt5_handler", None)

    def _loop():
        while not (stop_event is not None and stop_event.is_set()):
            try:
                connected = bool(handler.is_connected()) if handler is not None else False
            except Exception:
                connected = False
            runtime.uptime.record(connected)
            if stop_event is not None:
                if stop_event.wait(interval):
                    break
            else:
                time.sleep(interval)

    thread = threading.Thread(target=_loop, name="mt5-uptime-sampler", daemon=True)
    thread.start()
    return thread


def create_app(matcher=None, handler=None, notifier=None) -> Flask:
    """Application factory.

    Parameters
    ----------
    matcher, handler, notifier:
        Optional pre-built collaborators. When omitted, real instances are
        created (``MT5Handler`` connects to the terminal at construction time).
    """
    app = Flask(__name__)

    plan_matcher = matcher if matcher is not None else PlanMatcher()
    mt5_handler = handler if handler is not None else MT5Handler()
    notifier_obj = notifier if notifier is not None else Notifier()
    register_gui(app, plan_matcher)

    # --- Startup notification -------------------------------------------
    # A one-line "I'm alive" ping so an NSSM restart / VPS reboot is visible.
    # Suppressed entirely when Telegram is not configured.
    if Config.is_telegram_enabled() and hasattr(notifier_obj, "send_startup_message"):
        try:
            mt5_connected = bool(mt5_handler.is_connected())
            plan_count = len(plan_matcher.get_active_plans())
            notifier_obj.send_startup_message(
                mt5_connected=mt5_connected,
                plan_count=plan_count,
                live_trading=bool(Config.LIVE_TRADING),
            )
            logger.info(
                "Startup notification sent (mt5_connected=%s, plans=%d, live=%s).",
                mt5_connected,
                plan_count,
                Config.LIVE_TRADING,
            )
        except Exception as exc:
            logger.error("Startup notification failed: %s", exc)

    # Shared duplicate-detection state (fingerprint -> last seen monotonic ts).
    recent_signals: dict[str, float] = {}
    recent_lock = threading.Lock()

    # Runtime state surfaced by /health/full (uptime, last webhook, counts).
    runtime_state: dict = {
        "start_monotonic": time.monotonic(),
        # Counts every request that reaches a webhook endpoint, including those
        # that fail auth or carry malformed JSON - this is what makes
        # last_webhook_received meaningful for detecting a broken tunnel.
        "last_webhook_received": None,  # ISO-8601 UTC string
        "last_webhook_source": None,    # "tradingview" | "mt5"
        "webhook_count": 0,
        # Subset that passed the API-key check.
        "last_authorized_webhook_received": None,
        "authorized_webhook_count": 0,
        "unauthorized_count": 0,
    }
    runtime_lock = threading.Lock()

    def _record_webhook(source: str, authorized: bool = False) -> None:
        """Record an inbound webhook request (for the /health/full endpoint).

        Called at the *top* of every webhook handler, before auth/parsing, so
        that a stale ``last_webhook_received`` reliably means "the tunnel or
        sender is broken" rather than "a webhook arrived but was rejected".
        """
        now = utc_now_iso()
        with runtime_lock:
            if authorized:
                # Second call for the same request: only the authorized subset.
                runtime_state["last_authorized_webhook_received"] = now
                runtime_state["authorized_webhook_count"] = int(
                    runtime_state.get("authorized_webhook_count", 0)
                ) + 1
            else:
                # First call at the top of the handler: this is the per-request
                # counter used to detect a broken tunnel.
                runtime_state["last_webhook_received"] = now
                runtime_state["last_webhook_source"] = source
                runtime_state["webhook_count"] = int(runtime_state.get("webhook_count", 0)) + 1

    def _check_api_key() -> bool:
        """Return True when the request carries the correct API key."""
        provided = request.headers.get("X-API-Key", "")
        if not provided or provided != Config.API_KEY:
            logger.warning(
                "Rejected request to %s: invalid or missing X-API-Key (from %s)",
                request.path,
                request.remote_addr,
            )
            with runtime_lock:
                runtime_state["unauthorized_count"] = int(
                    runtime_state.get("unauthorized_count", 0)
                ) + 1
            return False
        return True

    def _get_json_payload():
        """Return ``(payload, error_response)``; payload is a dict or None."""
        payload = request.get_json(silent=True)
        if payload is None:
            # Fall back to raw-body JSON (content-type may be text/plain, which
            # is what TradingView sends by default).
            raw = request.get_data(as_text=True) or ""
            raw = raw.strip()
            if raw:
                import json

                try:
                    payload = json.loads(raw)
                except (ValueError, TypeError):
                    return None, _json_error("Body is not valid JSON.", 400)
        if payload is None:
            return None, _json_error("Request body is missing or not JSON.", 400)
        if not isinstance(payload, dict):
            return None, _json_error("JSON body must be an object.", 400)
        return payload, None

    def _register_signal(fingerprint: str) -> bool:
        """Record a signal fingerprint; return True if it is a duplicate."""
        now = time.monotonic()
        with recent_lock:
            # Opportunistically evict stale entries.
            for key in [k for k, ts in recent_signals.items() if now - ts > DUPLICATE_WINDOW_SEC]:
                recent_signals.pop(key, None)
            duplicate = fingerprint in recent_signals
            recent_signals[fingerprint] = now
        if duplicate:
            logger.warning("Duplicate signal within %ss window: %s", DUPLICATE_WINDOW_SEC, fingerprint)
        return duplicate

    def _evaluate_hypotheses(signal: dict) -> list[dict]:
        """Evaluate matching hypotheses without entering execution."""
        observations = []
        for item in plan_matcher.evaluate_hypothesis_market(signal):
            plan = item["plan"]
            evaluation = item["evaluation"]
            for event_type in evaluation.get("matches", []):
                event = {"event_type": event_type, "description": f"Condition matched: {event_type}", "source": "market", "market": signal}
                result = plan_matcher.process_hypothesis_event(plan.get("id"), event)
                new_status = result.get("hypothesis_status") if result else normalize_status(plan.get("hypothesis_status"))
                if result and result.get("ignored"):
                    continue
                try:
                    notifier_obj.send_hypothesis_event(plan, event_type, signal, new_status)
                except Exception as exc:
                    logger.warning("Hypothesis notification failed: %s", exc)
                observations.append({"plan_id": plan.get("id"), "event_type": event_type,
                                     "hypothesis_status": new_status})
        return observations

    def _record_hypothesis_market_event(plan: dict, signal: dict) -> tuple[str, dict | None]:
        """Record an explicit market event and advance a GUI hypothesis."""
        if "hypothesis_status" not in plan:
            return "legacy", None
        event_type = str(signal.get("event_type") or signal.get("hypothesis_event") or signal.get("condition_type") or "development").strip().lower()
        transitions = {
            "trigger": ("developing", "Trigger reached"),
            "confirmation": ("confirmed", "Confirmation reached"),
            "confirm": ("confirmed", "Confirmation reached"),
            "invalidation": ("invalidated", "Invalidation reached"),
            "invalidate": ("invalidated", "Invalidation reached"),
            "target": ("completed", "Target reached"),
        }
        record_event(plan, event_type, str(signal.get("description") or signal.get("condition") or ""), "market", signal)
        target = transitions.get(event_type)
        if target:
            try:
                transition(plan, target[0], target[1], "market", signal)
            except ValueError:
                logger.info("Hypothesis %s retained state after %s event", plan.get("id"), event_type)
        plan_matcher._persist()
        return event_type, {"status": normalize_status(plan.get("hypothesis_status")), "event_type": event_type}

    def _run_plan(plan: dict, signal: dict) -> dict:
        """Execute a matched plan and notify. Returns the execution result."""
        result = mt5_handler.execute_plan(plan, signal)
        try:
            notifier_obj.send_signal_alert(signal, plan, result)
        except Exception as exc:  # notification must never break trading flow
            logger.error("send_signal_alert failed: %s", exc)
        return result

    # ------------------------------------------------------------------ #
    # Health
    # ------------------------------------------------------------------ #
    @app.get("/health")
    def health():
        """Report liveness. 200 when MT5 is connected, else 503."""
        try:
            connected = bool(mt5_handler.is_connected())
        except Exception as exc:
            logger.error("Health check failed to query MT5: %s", exc)
            connected = False
        status = "healthy" if connected else "degraded"
        return jsonify({"status": status, "mt5_connected": connected}), (200 if connected else 503)

    @app.get("/health/full")
    def health_full():
        """Detailed status report for monitoring / watchdogs.

        Returns MT5 connection state, the timestamp of the last webhook received,
        the current open-position count and server uptime. Mirrors ``/health``:
        200 when MT5 is connected, 503 otherwise.
        """
        try:
            connected = bool(mt5_handler.is_connected())
        except Exception as exc:
            logger.error("Health(full) check failed to query MT5: %s", exc)
            connected = False

        open_positions = 0
        positions_error = None
        try:
            positions = mt5_handler.get_positions() or {}
            open_positions = int(positions.get("count", 0) or 0)
            positions_error = positions.get("error")
        except Exception as exc:
            logger.error("Health(full) failed to query positions: %s", exc)
            positions_error = str(exc)

        stale_plans = 0
        stale_plan_ids: list = []
        try:
            if hasattr(plan_matcher, "get_stale_plans"):
                stale = plan_matcher.get_stale_plans() or []
                stale_plan_ids = [p.get("id") for p in stale]
                stale_plans = len(stale)
        except Exception as exc:
            logger.error("Health(full) failed to compute stale plans: %s", exc)

        with runtime_lock:
            uptime = max(0.0, time.monotonic() - runtime_state["start_monotonic"])
            last_webhook = runtime_state["last_webhook_received"]
            last_source = runtime_state["last_webhook_source"]
            webhook_count = int(runtime_state.get("webhook_count", 0))
            last_auth_webhook = runtime_state.get("last_authorized_webhook_received")
            auth_webhook_count = int(runtime_state.get("authorized_webhook_count", 0))
            unauthorized_count = int(runtime_state.get("unauthorized_count", 0))

        payload = {
            "status": "healthy" if connected else "degraded",
            "mt5_connected": connected,
            "live_trading_effective": runtime.flags.live_trading_effective(),
            "panic": runtime.flags.is_trading_forced_off(),
            "last_webhook_received": last_webhook,
            "last_webhook_source": last_source,
            "webhook_count": webhook_count,
            "last_authorized_webhook_received": last_auth_webhook,
            "authorized_webhook_count": auth_webhook_count,
            "unauthorized_count": unauthorized_count,
            "open_positions": open_positions,
            "stale_plans": stale_plans,
            "stale_plan_ids": stale_plan_ids,
            "uptime_seconds": round(uptime, 3),
            "server_time_utc": utc_now_iso(),
        }
        if positions_error:
            payload["positions_error"] = positions_error
        return jsonify(payload), (200 if connected else 503)

    # ------------------------------------------------------------------ #
    # TradingView webhook
    # ------------------------------------------------------------------ #
    @app.post("/webhook/tradingview")
    def webhook_tradingview():
        """Receive a TradingView alert and act on any matching plan."""
        _record_webhook("tradingview")  # every request, even 401/400
        if not _check_api_key():
            return _json_error("Invalid or missing API key.", 401)
        try:
            payload, err = _get_json_payload()
            if err:
                return err

            _record_webhook("tradingview", authorized=True)
            logger.info("TradingView webhook payload: %s", payload)

            symbol = str(payload.get("symbol", "")).strip().upper()
            if not symbol:
                return _json_error("Payload must include 'symbol'.", 400)

            fingerprint = f"tv:{symbol}:{payload.get('action') or payload.get('direction')}:{payload.get('price')}"
            _register_signal(fingerprint)

            # Hypothesis observation is deliberately evaluated before legacy
            # execution matching. Alert/observe hypotheses never reach MT5.
            observations = _evaluate_hypotheses(payload)
            if observations:
                return jsonify({"status": "observed", "matched": True, "executed": False, "hypotheses": observations})
            hypothesis = plan_matcher.match_hypothesis(payload)
            if hypothesis is not None and str(hypothesis.get("execution_mode", "alert")).strip().lower() != "auto_execute":
                event_type, state = _record_hypothesis_market_event(hypothesis, payload)
                return jsonify({"status": "observed", "matched": True, "executed": False,
                                "hypothesis": True, "plan_id": hypothesis.get("id"),
                                "event_type": event_type, "hypothesis_status": state.get("status") if state else normalize_status(hypothesis.get("hypothesis_status"))})

            plan = plan_matcher.match(payload)
            if plan is None:
                try:
                    notifier_obj.send_unplanned_alert(payload)
                except Exception as exc:
                    logger.error("send_unplanned_alert failed: %s", exc)
                logger.warning("TradingView signal had no matching active plan: %s", payload)
                return jsonify({"status": "received", "matched": False, "executed": False})

            runtime.trigger_log.record(plan.get("id"))
            result = _run_plan(plan, payload)
            return jsonify(
                {
                    "status": "received",
                    "matched": True,
                    "executed": bool(result.get("success")),
                    "plan_id": plan.get("id"),
                    "order": result.get("order"),
                }
            )
        except Exception as exc:
            logger.exception("Unhandled error in /webhook/tradingview: %s", exc)
            return _json_error(str(exc), 500)

    # ------------------------------------------------------------------ #
    # MT5 EA webhook
    # ------------------------------------------------------------------ #
    @app.post("/webhook/mt5")
    def webhook_mt5():
        """Receive an MQL5 chart-object interaction and act on any matching plan."""
        _record_webhook("mt5")  # every request, even 401/400
        if not _check_api_key():
            return _json_error("Invalid or missing API key.", 401)
        try:
            payload, err = _get_json_payload()
            if err:
                return err

            _record_webhook("mt5", authorized=True)
            logger.info("MT5 webhook payload: %s", payload)

            object_name = str(payload.get("object_name", "")).strip()
            if not object_name:
                return _json_error("Payload must include 'object_name'.", 400)

            fingerprint = f"mt5:{object_name}:{payload.get('event')}:{payload.get('price')}"
            _register_signal(fingerprint)

            hypothesis = plan_matcher.match_hypothesis(payload)
            if hypothesis is not None and str(hypothesis.get("execution_mode", "alert")).strip().lower() != "auto_execute":
                signal = dict(payload)
                signal.setdefault("symbol", hypothesis.get("symbol"))
                signal.setdefault("timeframe", hypothesis.get("timeframe"))
                event_type, state = _record_hypothesis_market_event(hypothesis, signal)
                return jsonify({"status": "observed", "matched": True, "executed": False,
                                "hypothesis": True, "plan_id": hypothesis.get("id"),
                                "event_type": event_type, "hypothesis_status": state.get("status") if state else normalize_status(hypothesis.get("hypothesis_status"))})

            plan = plan_matcher.match_by_object(object_name)
            if plan is None:
                try:
                    notifier_obj.send_unplanned_alert(payload)
                except Exception as exc:
                    logger.error("send_unplanned_alert failed: %s", exc)
                logger.warning("MT5 object had no matching active plan: %s", object_name)
                return jsonify({"status": "received", "matched": False, "executed": False})

            # Enrich the signal with plan context so the notification is complete.
            signal = dict(payload)
            signal.setdefault("symbol", plan.get("symbol"))
            signal.setdefault("action", plan.get("action"))
            signal.setdefault("timeframe", plan.get("timeframe"))

            runtime.trigger_log.record(plan.get("id"))
            result = _run_plan(plan, signal)
            return jsonify(
                {
                    "status": "received",
                    "matched": True,
                    "executed": bool(result.get("success")),
                    "plan_id": plan.get("id"),
                    "order": result.get("order"),
                }
            )
        except Exception as exc:
            logger.exception("Unhandled error in /webhook/mt5: %s", exc)
            return _json_error(str(exc), 500)

    # ------------------------------------------------------------------ #
    # Kill switch / control
    # ------------------------------------------------------------------ #
    @app.post("/panic")
    def panic():
        """Engage the kill switch: force trading off at runtime (no .env edit)."""
        if not _check_api_key():
            return _json_error("Invalid or missing API key.", 401)
        try:
            previous = runtime.flags.live_trading_effective()
            runtime.flags.force_disable_trading("panic")
            logger.warning("KILL SWITCH engaged via /panic - trading disabled at runtime.")
            return jsonify(
                {
                    "status": "ok",
                    "panic": True,
                    "previous_live_trading_effective": previous,
                    "live_trading_effective": runtime.flags.live_trading_effective(),
                }
            )
        except Exception as exc:
            logger.exception("Unhandled error in /panic: %s", exc)
            return _json_error(str(exc), 500)

    @app.post("/resume")
    def resume():
        """Clear the kill switch (won't re-enable if .env disables trading)."""
        if not _check_api_key():
            return _json_error("Invalid or missing API key.", 401)
        try:
            previous = runtime.flags.live_trading_effective()
            runtime.flags.resume_trading()
            logger.warning("KILL SWITCH cleared via /resume.")
            return jsonify(
                {
                    "status": "ok",
                    "panic": False,
                    "previous_live_trading_effective": previous,
                    "live_trading_effective": runtime.flags.live_trading_effective(),
                }
            )
        except Exception as exc:
            logger.exception("Unhandled error in /resume: %s", exc)
            return _json_error(str(exc), 500)

    @app.get("/stats/today")
    def stats_today():
        """Today's operational stats (UTC), consumed by the daily digest."""
        if not _check_api_key():
            return _json_error("Invalid or missing API key.", 401)
        try:
            histogram = {}
            if hasattr(mt5_handler, "get_retcode_counts"):
                histogram = mt5_handler.get_retcode_counts()

            done = {str(c) for c in (10008, 10009, 10010)}
            failures = [(k, v) for k, v in histogram.items() if str(k) not in done]
            failures.sort(key=lambda kv: kv[1], reverse=True)
            top = [
                {
                    "retcode": int(k) if str(k).lstrip("-").isdigit() else k,
                    "count": v,
                    "text": describe_retcode(k),
                }
                for k, v in failures[:5]
            ]

            active = len(plan_matcher.get_active_plans())
            stale = (
                plan_matcher.count_stale_plans()
                if hasattr(plan_matcher, "count_stale_plans")
                else 0
            )

            with runtime_lock:
                unauthorized = int(runtime_state.get("unauthorized_count", 0))
                webhook_count = int(runtime_state.get("webhook_count", 0))

            return jsonify(
                {
                    "date_utc": utc_date_str(),
                    "mt5_connected": bool(mt5_handler.is_connected()),
                    "live_trading_effective": runtime.flags.live_trading_effective(),
                    "panic": runtime.flags.is_trading_forced_off(),
                    "mt5_uptime_percent": runtime.uptime.uptime_percent(),
                    "mt5_uptime_samples": runtime.uptime.sample_count(),
                    "plans_triggered_today": runtime.trigger_log.today_count(),
                    "triggered_plan_ids": runtime.trigger_log.today_ids(),
                    "active_plans": active,
                    "stale_plans": stale,
                    "unauthorized_count": unauthorized,
                    "webhook_count": webhook_count,
                    "retcode_histogram": histogram,
                    "top_non_done_retcodes": top,
                }
            )
        except Exception as exc:
            logger.exception("Unhandled error in /stats/today: %s", exc)
            return _json_error(str(exc), 500)

    # ------------------------------------------------------------------ #
    # Positions
    # ------------------------------------------------------------------ #
    @app.get("/positions")
    def positions():
        """Return all open MT5 positions."""
        if not _check_api_key():
            return _json_error("Invalid or missing API key.", 401)
        try:
            return jsonify(mt5_handler.get_positions())
        except Exception as exc:
            logger.exception("Unhandled error in /positions: %s", exc)
            return _json_error(str(exc), 500)

    @app.post("/close/<int:ticket>")
    def close(ticket: int):
        """Close the position identified by ``ticket``."""
        if not _check_api_key():
            return _json_error("Invalid or missing API key.", 401)
        try:
            result = mt5_handler.close_position(ticket)
            code = 200 if result.get("success") else 400
            return jsonify(result), code
        except Exception as exc:
            logger.exception("Unhandled error in /close/%s: %s", ticket, exc)
            return _json_error(str(exc), 500)

    # --- Hypothesis observation loop ----------------------------------
    observer_stop = threading.Event()
    # Polling can see the same completed candle many times. Keep a bounded
    # runtime cache; persistent event metadata provides the restart-safe guard.
    evaluated_bars: set[tuple[str, str, str]] = set()
    evaluated_bars_lock = threading.Lock()
    MAX_EVALUATED_BARS = 2000

    def _bar_observation_key(symbol: str, timeframe: str, context: dict) -> tuple[str, str, str] | None:
        event = str(context.get("event", "")).strip().lower()
        timestamp = context.get("timestamp")
        if event not in ("bar_close", "bar_closed") or not timestamp:
            return None
        return (
            str(symbol).strip().upper(),
            str(timeframe).strip().upper(),
            str(timestamp),
        )

    def _already_evaluated_bar(key: tuple[str, str, str]) -> bool:
        with evaluated_bars_lock:
            if key in evaluated_bars:
                return True
            evaluated_bars.add(key)
            if len(evaluated_bars) > MAX_EVALUATED_BARS:
                evaluated_bars.pop()
            return False

    def _hypothesis_observer():
        while not observer_stop.is_set():
            try:
                # Group active hypotheses by market context. Each unique
                # (symbol, timeframe) gets exactly one MT5 context fetch per
                # observer cycle; all hypotheses in that group are then
                # evaluated against the same snapshot.
                groups: dict[tuple[str, str], list[dict]] = {}
                for plan in list(plan_matcher.plans):
                    status = normalize_status(plan.get("hypothesis_status", "watching"))
                    if "hypothesis_status" not in plan or status in ("invalidated", "completed", "expired", "paused"):
                        continue
                    symbol = str(plan.get("symbol", "")).strip().upper()
                    timeframe = str(plan.get("timeframe", "H1")).strip().upper() or "H1"
                    if not symbol:
                        continue
                    groups.setdefault((symbol, timeframe), []).append(plan)

                for (symbol, timeframe), plans in groups.items():
                    # One market-context fetch for this symbol/timeframe.
                    context = mt5_handler.get_market_context(symbol, timeframe)
                    if context.get("error"):
                        continue

                    bar_key = _bar_observation_key(symbol, timeframe, context)
                    if bar_key is not None and _already_evaluated_bar(bar_key):
                        continue

                    observations = _evaluate_hypotheses(context)
                    if observations:
                        logger.info(
                            "Hypothesis observer processed %d observation(s) for %s %s @ %s across %d hypothesis(es)",
                            len(observations),
                            symbol,
                            timeframe,
                            context.get("timestamp"),
                            len(plans),
                        )
            except Exception as exc:
                logger.warning("Hypothesis observer cycle failed: %s", exc)
            observer_stop.wait(15)

    observer_thread = threading.Thread(target=_hypothesis_observer, name="hypothesis-observer", daemon=True)
    observer_thread.start()

    # Expose collaborators for tests / introspection without re-importing.
    app.plan_matcher = plan_matcher  # type: ignore[attr-defined]
    app.mt5_handler = mt5_handler  # type: ignore[attr-defined]
    app.notifier = notifier_obj  # type: ignore[attr-defined]
    app.runtime_state = runtime_state  # type: ignore[attr-defined]
    return app


_APP: Flask | None = None
_APP_LOCK = threading.Lock()


def get_app() -> Flask:
    """Return a process-wide singleton app, built lazily on first use."""
    global _APP
    with _APP_LOCK:
        if _APP is None:
            _APP = create_app()
        return _APP
