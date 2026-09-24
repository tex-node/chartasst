"""Trading Assistant entry point.

Run with::

    python main.py                # normal operation (honours LIVE_TRADING in .env)
    python main.py --dry-run      # force alert-only mode, ignore .env's LIVE_TRADING

Starts the Flask server on ``0.0.0.0:<FLASK_PORT>``, connects to the MT5
terminal, and writes logs to both stdout and a rotating file
(``logs/trading-assistant.log``). A graceful shutdown hook closes the MT5
connection on exit.
"""

from __future__ import annotations

import argparse
import atexit
import logging
import logging.handlers
import signal
import sys
import threading
from pathlib import Path

from app.config import Config
from app.notifier import TelegramCommander
from app.server import get_app, start_uptime_sampler

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"
LOG_FILENAME = "trading-assistant.log"
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
LOG_BACKUP_COUNT = 5

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


def build_arg_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser for the server."""
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Trading Assistant server (TradingView + MT5 signals -> MT5 + Telegram).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Force alert-only mode (LIVE_TRADING=false) regardless of .env. "
        "Signals are matched and notified but no orders are placed.",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Interface to bind (default: 0.0.0.0).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to listen on (default: FLASK_PORT from .env).",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Path to a .env file (default: <project_root>/.env).",
    )
    parser.add_argument(
        "--telegram-listen",
        action="store_true",
        help="Also run the Telegram command listener (/status, /plans, /panic, "
        "/resume, /close, /help). Off by default.",
    )
    return parser


def parse_args(argv=None) -> argparse.Namespace:
    """Parse command-line arguments. Exposed for testing."""
    return build_arg_parser().parse_args(argv)


def apply_dry_run(args: argparse.Namespace) -> bool:
    """Apply the ``--dry-run`` flag by forcing ``Config.LIVE_TRADING = False``.

    Returns ``True`` when dry-run mode was activated. Exposed for testing.
    """
    if getattr(args, "dry_run", False):
        Config.LIVE_TRADING = False
        return True
    return False


def configure_logging(log_dir=None, level: str | None = None):
    """Configure logging to stdout *and* a rotating file.

    Parameters
    ----------
    log_dir:
        Directory for the log file. Defaults to ``<project_root>/logs``.
    level:
        Log level name (e.g. ``"INFO"``). Defaults to ``Config.LOG_LEVEL``.

    Returns
    -------
    logging.handlers.RotatingFileHandler
        The file handler, so callers/tests can inspect it.
    """
    log_dir = Path(log_dir) if log_dir else DEFAULT_LOG_DIR
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / LOG_FILENAME

    resolved_level = getattr(logging, (level or Config.LOG_LEVEL or "INFO").upper(), logging.INFO)
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT)

    root = logging.getLogger()
    root.setLevel(resolved_level)

    # Remove only handlers *we* manage (StreamHandler covers Stream + File),
    # leaving pytest's capture handlers and any external handler untouched.
    for handler in list(root.handlers):
        if isinstance(handler, logging.StreamHandler):
            root.removeHandler(handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Quiet down the noisy Werkzeug request logger a touch.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    logging.getLogger(__name__).debug("Logging to %s (max %d bytes x %d backups)", log_path, LOG_MAX_BYTES, LOG_BACKUP_COUNT)
    return file_handler


def main(argv=None) -> int:
    """Boot the server. Returns a process exit code."""
    args = parse_args(argv)

    # Optional custom .env path (Config already loaded the default at import).
    if args.env_file:
        Config.load(args.env_file)

    configure_logging()
    logger = logging.getLogger("main")

    if apply_dry_run(args):
        # Override *after* .env is loaded so it always wins.
        logger.warning("--dry-run: LIVE_TRADING forced to false (alert-only mode).")

    logger.info("=" * 72)
    logger.info("Trading Assistant starting up")
    logger.info("=" * 72)

    problems = Config.validate(require_mt5=Config.LIVE_TRADING)
    if problems:
        logger.warning("Configuration warnings (%d):", len(problems))
        for problem in problems:
            logger.warning("  - %s", problem)

    if not Config.LIVE_TRADING:
        logger.warning("LIVE_TRADING=false -> ALERT-ONLY mode. No orders will be placed.")

    port = args.port or Config.FLASK_PORT
    logger.info("Bind address     : %s:%s", args.host, port)
    logger.info("Telegram enabled : %s", Config.is_telegram_enabled())
    logger.info("Notion enabled   : %s", Config.is_notion_enabled())
    logger.info("Obsidian enabled : %s", Config.is_obsidian_enabled())
    logger.info("Live trading     : %s", Config.LIVE_TRADING)

    app = get_app()
    handler = getattr(app, "mt5_handler", None)

    # Sample MT5 connectivity for the /stats/today uptime percentage.
    stop_event = threading.Event()
    start_uptime_sampler(app, interval=60.0, stop_event=stop_event)

    # Optional Telegram control surface (long-polls for commands).
    if args.telegram_listen:
        commander = TelegramCommander(
            getattr(app, "plan_matcher", None),
            getattr(app, "mt5_handler", None),
            getattr(app, "notifier", None),
        )
        threading.Thread(
            target=commander.run_forever,
            kwargs={"stop_event": stop_event},
            name="telegram-listener",
            daemon=True,
        ).start()
        logger.info("Telegram command listener enabled (--telegram-listen).")
    else:
        logger.info("Telegram command listener disabled (pass --telegram-listen to enable).")

    def _shutdown(*_args) -> None:
        logger.info("Shutting down Trading Assistant...")
        stop_event.set()
        if handler is not None:
            try:
                handler.shutdown()
            except Exception as exc:  # pragma: no cover - best effort
                logger.error("Error during MT5 shutdown: %s", exc)
        logger.info("Goodbye.")

    atexit.register(_shutdown)

    # Handle Ctrl+C / termination signals gracefully where supported.
    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, lambda *_: sys.exit(0))
        except (ValueError, OSError):  # pragma: no cover - platform dependent
            pass

    try:
        # use_reloader=False is critical: the reloader would start MT5 twice.
        app.run(host=args.host, port=port, debug=False, use_reloader=False)
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        _shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
