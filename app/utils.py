"""Small, dependency-light helper functions shared across the application.

These helpers deliberately contain no MT5 or network code so that they can be
unit-tested on any operating system.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def resolve_symbol(symbol: str, suffix: str) -> str:
    """Append a broker suffix to a base symbol when needed.

    Many brokers expose symbols such as ``EURUSD.r`` or ``EURUSDm``. Trading
    plans are always written with the *base* symbol (``EURUSD``) and the suffix
    is applied only when talking to MT5.

    Parameters
    ----------
    symbol:
        The base symbol, e.g. ``"EURUSD"``.
    suffix:
        The broker suffix, e.g. ``".r"``. Empty/None means no change.

    Returns
    -------
    str
        The fully qualified symbol.

    Examples
    --------
    >>> resolve_symbol("EURUSD", ".r")
    'EURUSD.r'
    >>> resolve_symbol("EURUSD.r", ".r")
    'EURUSD.r'
    >>> resolve_symbol("EURUSD", "")
    'EURUSD'
    """
    if not symbol:
        return symbol
    symbol = symbol.strip()
    if not suffix:
        return symbol
    suffix = suffix.strip()
    if not suffix:
        return symbol
    if symbol.lower().endswith(suffix.lower()):
        return symbol
    return f"{symbol}{suffix}"


def format_price(price: float, digits: int) -> str:
    """Format ``price`` with exactly ``digits`` decimal places.

    Falls back to a plain string representation when the inputs are not
    numeric, so notification formatting never raises.
    """
    try:
        return f"{float(price):.{int(digits)}f}"
    except (TypeError, ValueError):
        return str(price)


def load_json(path: str | os.PathLike, default: dict | None = None) -> dict:
    """Load a JSON file, returning ``default`` when it is missing or invalid.

    A missing file is normal (e.g. on first run) and is logged at INFO level.
    A malformed file is logged at ERROR level and also yields ``default`` so a
    corrupted file never crashes the server.
    """
    p = Path(path)
    if not p.exists():
        logger.info("JSON file %s does not exist; using default", p)
        return dict(default) if default else {}
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            logger.error("JSON file %s does not contain an object; using default", p)
            return dict(default) if default else {}
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to read JSON file %s: %s", p, exc)
        return dict(default) if default else {}


def save_json(path: str | os.PathLike, data: dict) -> None:
    """Atomically write ``data`` as pretty-printed JSON to ``path``.

    The write goes to a temporary file in the same directory first and is then
    moved into place, so a crash mid-write cannot corrupt the existing file.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(p.parent), prefix=p.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp_name, p)
    except Exception:
        # Clean up the temp file on any failure and re-raise for the caller.
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
        raise


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (seconds precision)."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def utc_date_str() -> str:
    """Return the current UTC date as ``YYYY-MM-DD``.

    Used for Obsidian daily-note filenames so the whole journal shares a single
    clock (UTC), regardless of the VPS or broker timezone.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def utc_time_str() -> str:
    """Return the current UTC time as ``HH:MM:SS``."""
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


# Kept for backwards compatibility; the journal now uses UTC throughout.
def local_date_str() -> str:
    """Return the local date as ``YYYY-MM-DD`` (legacy helper)."""
    return datetime.now().strftime("%Y-%m-%d")


def local_datetime_str() -> str:
    """Return the local time as ``HH:MM:SS`` (legacy helper)."""
    return datetime.now().strftime("%H:%M:%S")
