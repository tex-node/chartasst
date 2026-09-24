"""Process-wide runtime state that is not part of static configuration.

Three singletons live here:

* :data:`flags`        - the runtime **kill switch** (``/panic`` / ``/resume``).
* :data:`uptime`       - MT5 connection samples for an uptime percentage.
* :data:`trigger_log`  - timestamps of plans that fired today.

Keeping these out of :class:`app.config.Config` means a runtime kill switch
never edits ``.env`` and is trivially resettable between tests.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from app.config import Config
from app.utils import utc_date_str, utc_now_iso


class RuntimeFlags:
    """In-memory trading overrides (the kill switch).

    ``live_trading_effective`` is ``True`` only when *both* the static
    ``Config.LIVE_TRADING`` (.env) value **and** the runtime override allow
    trading. ``/panic`` forces the runtime override off; ``/resume`` clears it.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._trading_forced_off = False
        self._reason: str | None = None

    def force_disable_trading(self, reason: str = "panic") -> bool:
        """Force trading off at runtime. Returns the previous override state."""
        with self._lock:
            previous = self._trading_forced_off
            self._trading_forced_off = True
            self._reason = reason
            return previous

    def resume_trading(self) -> bool:
        """Clear the runtime override. Returns the previous override state."""
        with self._lock:
            previous = self._trading_forced_off
            self._trading_forced_off = False
            self._reason = None
            return previous

    def is_trading_forced_off(self) -> bool:
        """True when the runtime kill switch is engaged."""
        with self._lock:
            return self._trading_forced_off

    @property
    def reason(self) -> str | None:
        """Why trading was forced off (e.g. ``"panic"``), or ``None``."""
        with self._lock:
            return self._reason

    def live_trading_effective(self, env_value: bool | None = None) -> bool:
        """True only when the .env value *and* the runtime override both allow it."""
        if env_value is None:
            env_value = Config.LIVE_TRADING
        with self._lock:
            return bool(env_value) and not self._trading_forced_off

    def reset(self) -> None:
        """Clear all runtime overrides (used by tests)."""
        with self._lock:
            self._trading_forced_off = False
            self._reason = None


class UptimeTracker:
    """Collects periodic MT5 connection samples for the current UTC day."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._samples: list[dict] = []

    def record(self, connected: bool) -> None:
        """Record one connection sample and drop samples from previous days."""
        today = utc_date_str()
        with self._lock:
            self._samples = [s for s in self._samples if s["ts"][:10] == today]
            self._samples.append({"ts": utc_now_iso(), "connected": bool(connected)})

    def uptime_percent(self) -> float | None:
        """Percentage of today's samples that were connected, or ``None``."""
        with self._lock:
            total = len(self._samples)
            if total == 0:
                return None
            up = sum(1 for s in self._samples if s["connected"])
            return round(100.0 * up / total, 2)

    def sample_count(self) -> int:
        """Number of samples recorded today."""
        with self._lock:
            return len(self._samples)

    def reset(self) -> None:
        """Clear all samples (used by tests)."""
        with self._lock:
            self._samples = []


class TriggerLog:
    """Records the plans that fired today (matched a webhook)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: list[dict] = []

    def record(self, plan_id) -> None:
        """Record a trigger for ``plan_id``, dropping previous-day events."""
        today = utc_date_str()
        with self._lock:
            self._events = [e for e in self._events if e["ts"][:10] == today]
            self._events.append({"ts": utc_now_iso(), "plan_id": plan_id})

    def today_count(self) -> int:
        """Number of triggers recorded today."""
        with self._lock:
            return len(self._events)

    def today_ids(self) -> list:
        """Plan ids that triggered today (may contain duplicates)."""
        with self._lock:
            return [e["plan_id"] for e in self._events]

    def reset(self) -> None:
        """Clear all events (used by tests)."""
        with self._lock:
            self._events = []


def _now_utc() -> datetime:
    """Small helper retained for future use / testing."""
    return datetime.now(timezone.utc)


# Module-level singletons - import these directly.
flags = RuntimeFlags()
uptime = UptimeTracker()
trigger_log = TriggerLog()


def live_trading_effective(env_value: bool | None = None) -> bool:
    """Convenience wrapper around :meth:`RuntimeFlags.live_trading_effective`."""
    return flags.live_trading_effective(env_value)
