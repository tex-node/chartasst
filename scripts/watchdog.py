"""Standalone health watchdog for the Trading Assistant.

Polls the server's ``/health`` endpoint (default every 3 minutes) and sends a
Telegram alert when it returns a non-200 response **twice in a row**. Designed
to run either as a long-lived process or as a cron / Task Scheduler job with
``--once``.

Usage
-----
::

    python scripts/watchdog.py                 # loop forever, every 180s
    python scripts/watchdog.py --once          # single check, exit (cron mode)
    python scripts/watchdog.py --interval 60   # custom interval (seconds)
    python scripts/watchdog.py --url http://127.0.0.1:5000/health
    python scripts/watchdog.py --dry-run       # log alerts instead of sending
    python scripts/watchdog.py --ping-url https://hc-ping.com/<uuid>   # dead-man's switch

Configuration is read from the environment (a local ``.env`` is loaded when
``python-dotenv`` is available):

- ``TELEGRAM_BOT_TOKEN``  / ``TELEGRAM_CHAT_ID`` - alert destination
- ``WATCHDOG_URL``        - health URL (default http://127.0.0.1:5000/health)
- ``WATCHDOG_INTERVAL``   - seconds between checks (default 180)
- ``WATCHDOG_TIMEOUT``    - request timeout in seconds (default 10)
- ``WATCHDOG_PING_URL``   - external dead-man's-switch URL pinged on success
                            (e.g. a healthchecks.io check URL)

Exit codes: ``0`` success, ``1`` a check failed, ``2`` bad arguments.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import requests

try:  # .env support is optional for the watchdog
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None  # type: ignore

logger = logging.getLogger("watchdog")

DEFAULT_URL = "http://127.0.0.1:5000/health"
DEFAULT_INTERVAL = 180  # seconds (3 minutes)
DEFAULT_TIMEOUT = 10
FAILURE_THRESHOLD = 2


def check_health(url: str, timeout: int = DEFAULT_TIMEOUT, session=None):
    """Perform one health request.

    Returns
    -------
    tuple[int, dict | None]
        ``(status_code, body)``. A network error is reported as ``(0, None)``
        so the caller treats it exactly like a failed check.
    """
    http = session if session is not None else requests
    try:
        response = http.get(url, timeout=timeout)
        try:
            body = response.json()
        except ValueError:
            body = None
        return response.status_code, body
    except requests.RequestException as exc:
        logger.error("Health request to %s failed: %s", url, exc)
        return 0, None


def send_telegram(token: str, chat_id: str, message: str, timeout: int = DEFAULT_TIMEOUT, session=None) -> bool:
    """Send a Telegram message. Returns success. Never raises."""
    if not token or not chat_id:
        logger.warning("Telegram not configured; watchdog alert not sent.")
        return False

    http = session if session is not None else requests
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    try:
        response = http.post(url, json=payload, timeout=timeout)
        ok = response.status_code == 200 and bool(response.json().get("ok"))
        if ok:
            logger.info("Watchdog alert sent.")
        else:
            logger.error("Watchdog Telegram error: HTTP %s %s", response.status_code, getattr(response, "text", "")[:200])
        return ok
    except (requests.RequestException, ValueError) as exc:
        logger.error("Watchdog Telegram request failed: %s", exc)
        return False


def ping_external(ping_url: str, timeout: int = DEFAULT_TIMEOUT, session=None) -> bool:
    """Ping an external dead-man's-switch URL (e.g. healthchecks.io).

    Called on every *successful* local health check. If the VPS dies, the pings
    stop and the external service alerts you - covering the case where the
    watchdog itself dies with the machine. Never raises.
    """
    if not ping_url:
        return False
    http = session if session is not None else requests
    try:
        response = http.get(ping_url, timeout=timeout)
        logger.info("External ping sent (%s): HTTP %s", ping_url, response.status_code)
        return 200 <= response.status_code < 300
    except requests.RequestException as exc:
        logger.warning("External ping to %s failed: %s", ping_url, exc)
        return False


def send_email_alert(subject: str, body: str) -> bool:
    """Email an alert when SMTP is configured. Never raises."""
    try:
        repo = Path(__file__).resolve().parent.parent
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        from app.emailer import Emailer

        return Emailer().send(subject, body)
    except Exception as exc:
        logger.error("Watchdog email alert failed: %s", exc)
        return False


def run(
    once: bool = False,
    interval: int = DEFAULT_INTERVAL,
    url: str = DEFAULT_URL,
    timeout: int = DEFAULT_TIMEOUT,
    token: str = "",
    chat_id: str = "",
    ping_url: str = "",
    session=None,
    sleep=time.sleep,
    max_checks: int | None = None,
) -> int:
    """Poll ``url`` and alert after ``FAILURE_THRESHOLD`` consecutive failures.

    On every successful check, ``ping_url`` (if set) is pinged as an external
    dead-man's switch. Parameters mirror the CLI. ``sleep`` and ``max_checks``
    exist for testing.

    Returns ``0`` when every check passed, ``1`` if any check failed.
    """
    consecutive_failures = 0
    checks = 0
    had_failure = False

    while True:
        status_code, body = check_health(url, timeout=timeout, session=session)
        checks += 1

        if status_code == 200:
            if consecutive_failures:
                logger.info("Health recovered after %d failure(s).", consecutive_failures)
            consecutive_failures = 0
            logger.info("Health OK (HTTP 200)%s", f" {body}" if body else "")
            # Dead-man's switch: only ping while healthy.
            if ping_url:
                ping_external(ping_url, timeout=timeout, session=session)
        else:
            consecutive_failures += 1
            had_failure = True
            logger.warning(
                "Health check failed (%d/%d): status=%s url=%s",
                consecutive_failures,
                FAILURE_THRESHOLD,
                status_code,
                url,
            )
            if consecutive_failures >= FAILURE_THRESHOLD:
                message = (
                    f"🚨 *Trading Assistant is DOWN*\n"
                    f"Health check failed {consecutive_failures} times in a row "
                    f"(HTTP {status_code}).\n"
                    f"URL: {url}\n"
                    f"Check the server, MT5 terminal and network."
                )
                send_telegram(token, chat_id, message, timeout=timeout, session=session)
                # Also email the alert if SMTP is configured.
                send_email_alert(
                    "[Trading Assistant] health check failing", message.replace("*", "")
                )
                # Reset so we do not spam an alert on every subsequent failure.
                consecutive_failures = 0

        if once or (max_checks is not None and checks >= max_checks):
            break
        sleep(interval)

    return 1 if had_failure else 0


def _load_env() -> None:
    """Best-effort load of a project-root .env file."""
    if load_dotenv is None:
        return
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)


def build_arg_parser() -> argparse.ArgumentParser:
    """Create the CLI parser for the watchdog."""
    parser = argparse.ArgumentParser(
        prog="watchdog.py",
        description="Poll the Trading Assistant /health endpoint and alert on failure.",
    )
    parser.add_argument("--once", action="store_true", help="Run a single check then exit (cron mode).")
    parser.add_argument("--interval", type=int, default=int(os.environ.get("WATCHDOG_INTERVAL", DEFAULT_INTERVAL)),
                        help="Seconds between checks (default 180).")
    parser.add_argument("--url", default=os.environ.get("WATCHDOG_URL", DEFAULT_URL),
                        help="Health endpoint URL.")
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("WATCHDOG_TIMEOUT", DEFAULT_TIMEOUT)),
                        help="HTTP timeout in seconds (default 10).")
    parser.add_argument("--ping-url", default=os.environ.get("WATCHDOG_PING_URL", ""),
                        help="External dead-man's-switch URL pinged on every successful check "
                             "(e.g. a healthchecks.io check URL).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Do not send Telegram alerts; log them instead.")
    return parser


def parse_args(argv=None) -> argparse.Namespace:
    """Parse command-line arguments. Exposed for testing."""
    return build_arg_parser().parse_args(argv)


def main(argv=None) -> int:
    """Entry point for the watchdog CLI."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    args = parse_args(argv)
    _load_env()

    token = "" if args.dry_run else os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = "" if args.dry_run else os.environ.get("TELEGRAM_CHAT_ID", "")

    logger.info(
        "Watchdog started: url=%s interval=%ss once=%s dry_run=%s ping_url=%s",
        args.url, args.interval, args.once, args.dry_run, args.ping_url or "(none)",
    )
    return run(
        once=args.once,
        interval=args.interval,
        url=args.url,
        timeout=args.timeout,
        token=token,
        chat_id=chat_id,
        ping_url=args.ping_url,
    )


if __name__ == "__main__":
    raise SystemExit(main())
