"""Trading-plan storage, matching, and status management.

A *plan* is the user's pre-written trading scenario, for example:

.. code-block:: json

    {
      "id": "plan_001",
      "name": "EURUSD H1 Resistance Breakout",
      "symbol": "EURUSD",
      "timeframe": "H1",
      "condition": "Price breaks above 1.0850 resistance",
      "object_name": "Resistance_1.0850",
      "action": "buy",
      "volume": 0.01,
      "sl_points": 200,
      "tp_points": 400,
      "status": "active",
      "notes": "Wait for H1 close above 1.0850"
    }

Plans live in ``plans/plans.json``. This module is pure Python (no MT5, no
network) so it is fully unit-testable anywhere.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.utils import load_json, save_json, utc_now_iso

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PLANS_PATH = PROJECT_ROOT / "plans" / "plans.json"

# Status values used by the system.
STATUS_ACTIVE = "active"
STATUS_TRIGGERED = "triggered"
STATUS_DISABLED = "disabled"
STATUS_EXPIRED = "expired"

# An active plan older than this many days is considered "stale" and worth
# reviewing (the setup it described has probably long passed).
STALE_PLAN_DAYS = 30

VALID_ACTIONS = ("buy", "sell")

# Signal "direction" values (used by the MT5 EA) mapped to an order action.
DIRECTION_TO_ACTION = {
    "above": "buy",
    "up": "buy",
    "bullish": "buy",
    "below": "sell",
    "down": "sell",
    "bearish": "sell",
}


class PlanMatcher:
    """Loads plans from JSON and matches incoming signals against them.

    The matcher is thread-safe (guarded by an :class:`RLock`) because Flask may
    serve several webhook requests concurrently.
    """

    def __init__(self, plans_path: str | Path | None = None) -> None:
        self.plans_path = Path(plans_path) if plans_path else DEFAULT_PLANS_PATH
        self._lock = threading.RLock()
        self.plans: list[dict] = []
        self.load()

    # ------------------------------------------------------------------ #
    # Loading / persistence
    # ------------------------------------------------------------------ #
    def load(self) -> list[dict]:
        """(Re)load plans from disk. Returns the loaded list."""
        with self._lock:
            data = load_json(self.plans_path, {"plans": []})
            plans = data.get("plans", [])
            if not isinstance(plans, list):
                logger.error(
                    "plans.json 'plans' key is not a list (%s); starting empty",
                    type(plans).__name__,
                )
                plans = []
            self.plans = plans
            logger.info("Loaded %d plan(s) from %s", len(plans), self.plans_path)
            return self.plans

    def reload(self) -> list[dict]:
        """Alias for :meth:`load` to make intent explicit at call sites."""
        return self.load()

    def _persist(self) -> None:
        """Write the current in-memory plans back to disk (locked caller)."""
        save_json(self.plans_path, {"plans": self.plans})

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def get_active_plans(self) -> list[dict]:
        """Return a copy of all plans whose status is ``active``."""
        with self._lock:
            return [p for p in self.plans if p.get("status") == STATUS_ACTIVE]

    def get_plan(self, plan_id: str) -> dict | None:
        """Return the plan with ``plan_id`` or ``None`` if not found."""
        with self._lock:
            for plan in self.plans:
                if plan.get("id") == plan_id:
                    return plan
            return None

    @staticmethod
    def _parse_created_at(value) -> datetime | None:
        """Parse a plan's ``created_at`` into an aware UTC datetime.

        Returns ``None`` when the value is missing or unparseable (such plans
        are simply not considered stale).
        """
        if not value:
            return None
        try:
            text = str(value).strip().replace("Z", "+00:00")
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except (ValueError, TypeError):
            return None

    def get_stale_plans(self, max_age_days: int = STALE_PLAN_DAYS) -> list[dict]:
        """Return active plans created more than ``max_age_days`` ago.

        Plans without a parseable ``created_at`` are ignored. Useful for a
        periodic "review your plans" reminder.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(max_age_days))
        stale: list[dict] = []
        with self._lock:
            for plan in self.plans:
                if plan.get("status") != STATUS_ACTIVE:
                    continue
                created = self._parse_created_at(plan.get("created_at"))
                if created is not None and created < cutoff:
                    stale.append(plan)
        return stale

    def count_stale_plans(self, max_age_days: int = STALE_PLAN_DAYS) -> int:
        """Return the number of active plans older than ``max_age_days``."""
        return len(self.get_stale_plans(max_age_days))

    # ------------------------------------------------------------------ #
    # Matching
    # ------------------------------------------------------------------ #
    @staticmethod
    def _normalize_action(signal: dict) -> str | None:
        """Derive a ``buy``/``sell`` action from a signal dictionary.

        Understands an explicit ``action`` or ``side`` field, and falls back to
        mapping a ``direction`` such as ``"above"`` (-> buy) or ``"below"``
        (-> sell).
        """
        for key in ("action", "side"):
            value = signal.get(key)
            if value:
                value = str(value).strip().lower()
                if value in VALID_ACTIONS:
                    return value
                # Allow "long"/"short" as friendly aliases.
                if value in ("long",):
                    return "buy"
                if value in ("short",):
                    return "sell"
        direction = str(signal.get("direction", "")).strip().lower()
        return DIRECTION_TO_ACTION.get(direction)

    def match(self, signal: dict) -> dict | None:
        """Match a TradingView signal by ``symbol`` **and** action.

        Parameters
        ----------
        signal:
            A parsed webhook payload, e.g.
            ``{"symbol": "EURUSD", "action": "buy", "price": 1.0851}``.

        Returns
        -------
        dict | None
            The first active plan matching symbol + action, or ``None``.
        """
        if not isinstance(signal, dict):
            logger.warning("match() received non-dict signal: %r", signal)
            return None

        symbol = str(signal.get("symbol", "")).strip().upper()
        action = self._normalize_action(signal)

        if not symbol or not action:
            logger.warning(
                "Signal missing symbol or a recognisable action: %s", signal
            )
            return None

        with self._lock:
            for plan in self.plans:
                if plan.get("status") != STATUS_ACTIVE:
                    continue
                if str(plan.get("symbol", "")).strip().upper() != symbol:
                    continue
                if str(plan.get("action", "")).strip().lower() != action:
                    continue
                logger.info(
                    "Signal %s %s matched plan %s", symbol, action, plan.get("id")
                )
                return plan

        logger.info("No active plan matched signal %s %s", symbol, action)
        return None

    def match_hypothesis(self, signal: dict) -> dict | None:
        """Match a non-executing hypothesis by symbol/timeframe."""
        if not isinstance(signal, dict):
            return None
        symbol = str(signal.get("symbol", "")).strip().upper()
        timeframe = str(signal.get("timeframe", "")).strip().upper()
        if not symbol:
            return None
        with self._lock:
            for plan in self.plans:
                if "hypothesis_status" not in plan:
                    continue
                if normalize_status(plan.get("hypothesis_status")) in ("invalidated", "completed", "expired"):
                    continue
                if str(plan.get("symbol", "")).strip().upper() != symbol:
                    continue
                plan_tf = str(plan.get("timeframe", "")).strip().upper()
                if timeframe and plan_tf and timeframe != plan_tf:
                    continue
                return plan
        return None

    def match_by_object(self, object_name: str) -> dict | None:
        """Match an MT5 chart-object interaction by ``object_name``.

        The match ignores status == active check? No - only active plans are
        considered, consistent with :meth:`match`.
        """
        if not object_name:
            logger.warning("match_by_object() called with empty object_name")
            return None

        target = str(object_name).strip().lower()
        with self._lock:
            for plan in self.plans:
                if plan.get("status") != STATUS_ACTIVE:
                    continue
                plan_object = str(plan.get("object_name", "")).strip().lower()
                if plan_object and plan_object == target:
                    logger.info(
                        "MT5 object %s matched plan %s", object_name, plan.get("id")
                    )
                    return plan

        logger.info("No active plan matched MT5 object %s", object_name)
        return None

    # ------------------------------------------------------------------ #
    # Mutations
    # ------------------------------------------------------------------ #
    def update_plan_status(self, plan_id: str, status: str) -> bool:
        """Persist a new status for ``plan_id``.

        Returns ``True`` when a plan was found and updated, ``False`` otherwise.
        """
        with self._lock:
            for plan in self.plans:
                if plan.get("id") == plan_id:
                    old = plan.get("status")
                    plan["status"] = status
                    self._persist()
                    logger.info(
                        "Plan %s status changed: %s -> %s", plan_id, old, status
                    )
                    return True
        logger.warning("update_plan_status: plan %s not found", plan_id)
        return False

    def add_plan(self, plan_data: dict) -> dict:
        """Add a new plan, assigning an ``id`` when absent, and persist it.

        Raises
        ------
        ValueError
            If required fields (``symbol`` or ``action``) are missing.
        """
        if not isinstance(plan_data, dict):
            raise ValueError("plan_data must be a dictionary")

        symbol = str(plan_data.get("symbol", "")).strip()
        action = str(plan_data.get("action", "")).strip().lower()
        if not symbol:
            raise ValueError("plan_data must include 'symbol'")
        if action not in VALID_ACTIONS:
            raise ValueError("plan_data 'action' must be 'buy' or 'sell'")

        plan = dict(plan_data)
        plan["symbol"] = symbol.upper()
        plan["action"] = action
        plan.setdefault("status", STATUS_ACTIVE)
        plan.setdefault("volume", None)
        plan.setdefault("sl_points", None)
        plan.setdefault("tp_points", None)
        plan.setdefault("object_name", "")
        plan.setdefault("name", f"{plan['symbol']} {plan['action']} plan")
        plan.setdefault("timeframe", "")
        plan.setdefault("condition", "")
        plan.setdefault("notes", "")
        # ISO-8601 UTC creation time, used for staleness reporting.
        plan.setdefault("created_at", utc_now_iso())

        with self._lock:
            if not plan.get("id"):
                plan["id"] = self._next_id()
            # Guard against duplicate ids.
            if any(p.get("id") == plan["id"] for p in self.plans):
                raise ValueError(f"plan id {plan['id']} already exists")
            self.plans.append(plan)
            self._persist()
            logger.info("Added plan %s (%s %s)", plan["id"], plan["symbol"], action)
            return plan

    def _next_id(self) -> str:
        """Generate the next ``plan_NNN`` identifier (locked caller)."""
        max_n = 0
        for plan in self.plans:
            pid = str(plan.get("id", ""))
            if pid.startswith("plan_"):
                try:
                    max_n = max(max_n, int(pid.split("_", 1)[1]))
                except (IndexError, ValueError):
                    continue
        return f"plan_{max_n + 1:03d}"

    def process_hypothesis_event(self, plan_id: str, event: dict) -> dict | None:
        """Record a market event and advance an explicitly typed hypothesis."""
        plan = self.get_plan(plan_id)
        if not plan:
            return None
        from app.hypothesis_state import record_event, transition, normalize_status
        event = event if isinstance(event, dict) else {}
        event_type = str(event.get("event_type") or event.get("type") or "development").strip().lower()
        transitions = {
            "trigger": ("developing", "Trigger reached"),
            "confirmation": ("confirmed", "Confirmation reached"),
            "confirm": ("confirmed", "Confirmation reached"),
            "invalidation": ("invalidated", "Invalidation reached"),
            "invalidate": ("invalidated", "Invalidation reached"),
            "target": ("completed", "Target reached"),
        }
        current = normalize_status(plan.get("hypothesis_status", "watching"))
        required_state = {
            "trigger": "watching",
            "confirmation": "developing",
            "confirm": "developing",
            "target": "confirmed",
        }
        required = required_state.get(event_type)
        if required and current != required:
            logger.info("Hypothesis %s ignored %s while state is %s", plan_id, event_type, current)
            return {"plan": plan, "event_type": event_type, "hypothesis_status": current, "ignored": True}
        record_event(plan, event_type, str(event.get("description") or event.get("condition") or ""), "market", event)
        target = transitions.get(event_type)
        if target:
            try:
                transition(plan, target[0], target[1], "market", event)
            except ValueError:
                logger.info("Hypothesis %s retained state after %s", plan_id, event_type)
        with self._lock:
            self._persist()
        return {"plan": plan, "event_type": event_type, "hypothesis_status": normalize_status(plan.get("hypothesis_status", "watching"))}

    def evaluate_hypothesis_market(self, signal: dict) -> list[dict]:
        """Evaluate all matching non-terminal hypotheses without execution."""
        from app.condition_engine import evaluate_hypothesis
        results = []
        if not isinstance(signal, dict):
            return results
        symbol = str(signal.get("symbol", "")).strip().upper()
        timeframe = str(signal.get("timeframe", "")).strip().upper()
        with self._lock:
            plans = list(self.plans)
        for plan in plans:
            if "hypothesis_status" not in plan or str(plan.get("symbol", "")).strip().upper() != symbol:
                continue
            if normalize_status(plan.get("hypothesis_status")) in ("invalidated", "completed", "expired", "paused"):
                continue
            plan_tf = str(plan.get("timeframe", "")).strip().upper()
            if timeframe and plan_tf and timeframe != plan_tf:
                continue
            evaluation = evaluate_hypothesis(plan, signal)
            if evaluation["matched"]:
                results.append({"plan": plan, "evaluation": evaluation})
        return results
