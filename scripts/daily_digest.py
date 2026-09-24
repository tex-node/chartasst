"""Daily digest for the Trading Assistant.

Queries ``/health/full`` and ``/stats/today`` and sends a single Telegram
message summarising the day (all times UTC):

- plans triggered today, active plan count, stale plan count
- MT5 uptime percentage since midnight UTC
- unauthorized webhook count
- the top 5 non-DONE order retcodes

Usage
-----
::

    python scripts/daily_digest.py                 # fetch and send
    python scripts/daily_digest.py --dry-run       # print only, no Telegram
    python scripts/daily_digest.py --url http://127.0.0.1:5000

Configuration is read from the environment / ``.env``:

- ``DIGEST_URL`` (or ``--url``)  - server base URL (default http://127.0.0.1:5000)
- ``API_KEY``                    - required to call /stats/today
- ``TELEGRAM_BOT_TOKEN`` / ``TELEGRAM_CHAT_ID`` - delivery
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

import requests

try:  # optional
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore

logger = logging.getLogger("daily_digest")

# Allow importing the app package when this file is run as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_BASE_URL = "http://127.0.0.1:5000"
DEFAULT_TIMEOUT = 10


def fetch_json(url: str, api_key: str = "", timeout: int = DEFAULT_TIMEOUT, session=None) -> dict:
    """GET ``url`` and return parsed JSON. Raises on HTTP/network errors."""
    http = session if session is not None else requests
    headers = {"X-API-Key": api_key} if api_key else {}
    response = http.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


def format_digest(health: dict, stats: dict) -> str:
    """Build the Markdown digest body from the two endpoint payloads."""
    health = health or {}
    stats = stats or {}

    uptime = stats.get("mt5_uptime_percent")
    uptime_text = f"{uptime}%" if uptime is not None else "n/a (no samples yet)"

    lines = [
        "📊 *Trading Assistant — daily digest*",
        f"🗓 {stats.get('date_utc', '')} (UTC)",
        f"⚙️ Live trading: {'ON' if health.get('live_trading_effective') else 'OFF'}"
        + (" 🚨 kill switch" if health.get("panic") else ""),
        f"🔌 MT5 connected: {'yes' if health.get('mt5_connected') else 'no'}",
        f"📈 MT5 uptime today: {uptime_text}",
        f"🎯 Plans triggered today: {stats.get('plans_triggered_today', 0)}",
        f"📋 Active plans: {stats.get('active_plans', 0)}",
        f"🗂 Stale plans (>30d): {stats.get('stale_plans', 0)}",
        f"🚫 Unauthorized webhooks: {stats.get('unauthorized_count', 0)}",
    ]

    top = stats.get("top_non_done_retcodes") or []
    if top:
        lines.append("❌ *Top non-DONE retcodes:*")
        for item in top[:5]:
            code = item.get("retcode")
            text = item.get("text") or "unknown"
            count = item.get("count", 0)
            lines.append(f"  • {code} ×{count} — {text}")
    else:
        lines.append("✅ No non-DONE order retcodes today.")

    return "\n".join(lines)


def send_telegram(token: str, chat_id: str, message: str, timeout: int = DEFAULT_TIMEOUT, session=None) -> bool:
    """Send the digest via Telegram. Never raises."""
    if not token or not chat_id:
        logger.warning("Telegram not configured; digest not sent.")
        return False
    http = session if session is not None else requests
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        response = http.post(
            url,
            json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
            timeout=timeout,
        )
        ok = response.status_code == 200 and bool(response.json().get("ok"))
        if not ok:
            logger.error("Telegram digest error: HTTP %s", response.status_code)
        return ok
    except (requests.RequestException, ValueError) as exc:
        logger.error("Telegram digest request failed: %s", exc)
        return False


def send_email_digest(message: str, stats: dict) -> bool:
    """Email the digest when SMTP is configured. Never raises."""
    try:
        from app.emailer import Emailer

        subject = f"[Trading Assistant] daily digest {stats.get('date_utc', '')}".strip()
        return Emailer().send(subject, (message or "").replace("*", ""))
    except Exception as exc:
        logger.error("Email digest failed: %s", exc)
        return False


def build_arg_parser() -> argparse.ArgumentParser:
    """Create the CLI parser for the digest."""
    parser = argparse.ArgumentParser(
        prog="daily_digest.py",
        description="Send a daily Trading Assistant digest to Telegram.",
    )
    parser.add_argument(
        "--url", default=os.environ.get("DIGEST_URL", DEFAULT_BASE_URL),
        help="Server base URL (default http://127.0.0.1:5000).",
    )
    parser.add_argument("--api-key", default=os.environ.get("API_KEY", ""),
                        help="X-API-Key for the server (defaults to API_KEY env).")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help="HTTP timeout in seconds (default 10).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the digest without sending it to Telegram.")
    return parser


def parse_args(argv=None) -> argparse.Namespace:
    """Parse CLI arguments. Exposed for testing."""
    return build_arg_parser().parse_args(argv)


def _load_env() -> None:
    """Best-effort load of a project-root .env file."""
    if load_dotenv is None:
        return
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)


def _configure_console_utf8() -> None:
    """Make stdout/stderr UTF-8 so emoji print on a cp1252 Windows console."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover - stream may not support it
            pass


def main(argv=None) -> int:
    """Fetch the stats, format the digest and (optionally) send it."""
    _configure_console_utf8()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    args = parse_args(argv)
    _load_env()

    base = args.url.rstrip("/")
    api_key = args.api_key or os.environ.get("API_KEY", "")

    try:
        health = fetch_json(f"{base}/health/full", api_key=None, timeout=args.timeout)
        stats = fetch_json(f"{base}/stats/today", api_key=api_key, timeout=args.timeout)
    except Exception as exc:
        logger.error("Could not fetch stats from %s: %s", base, exc)
        return 1

    message = format_digest(health, stats)
    print(message)

    if args.dry_run:
        logger.info("--dry-run: digest printed, not sent.")
        return 0

    sent_tg = send_telegram(
        os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        os.environ.get("TELEGRAM_CHAT_ID", ""),
        message,
        timeout=args.timeout,
    )
    sent_email = send_email_digest(message, stats)
    return 0 if (sent_tg or sent_email) else 1


if __name__ == "__main__":
    raise SystemExit(main())
