"""Unit tests for :mod:`main` (CLI flags and logging configuration)."""

import logging
import logging.handlers

import main
from app.config import Config


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------
def test_parse_args_defaults():
    args = main.parse_args([])
    assert args.dry_run is False
    assert args.host == "0.0.0.0"
    assert args.port is None
    assert args.env_file is None


def test_parse_args_dry_run_flag():
    assert main.parse_args(["--dry-run"]).dry_run is True


def test_parse_args_port_and_env_file():
    args = main.parse_args(["--port", "6000", "--env-file", "custom.env"])
    assert args.port == 6000
    assert args.env_file == "custom.env"


# --------------------------------------------------------------------------
# --dry-run behaviour
# --------------------------------------------------------------------------
def test_apply_dry_run_forces_alert_only(monkeypatch):
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    args = main.parse_args(["--dry-run"])
    assert main.apply_dry_run(args) is True
    assert Config.LIVE_TRADING is False


def test_apply_dry_run_is_noop_without_flag(monkeypatch):
    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    assert main.apply_dry_run(main.parse_args([])) is False
    assert Config.LIVE_TRADING is True


# --------------------------------------------------------------------------
# Rotating file logging
# --------------------------------------------------------------------------
def test_configure_logging_creates_rotating_file(tmp_path):
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    try:
        file_handler = main.configure_logging(log_dir=tmp_path, level="INFO")

        assert isinstance(file_handler, logging.handlers.RotatingFileHandler)
        assert file_handler.maxBytes == 10 * 1024 * 1024
        assert file_handler.backupCount == 5
        assert file_handler.baseFilename.endswith("trading-assistant.log")

        # A log record must land in the file.
        logging.getLogger("tests.file").info("proof-of-file-logging")
        file_handler.flush()
        content = (tmp_path / "trading-assistant.log").read_text(encoding="utf-8")
        assert "proof-of-file-logging" in content
        assert "tests.file" in content
    finally:
        # Restore the root logger exactly as it was.
        for handler in list(root.handlers):
            if isinstance(handler, logging.StreamHandler):
                root.removeHandler(handler)
        for handler in saved_handlers:
            root.addHandler(handler)


def test_configure_logging_creates_missing_parent_dirs(tmp_path):
    """First-deploy guard: the logs/ directory may not exist yet."""
    target = tmp_path / "deep" / "nested" / "logs"
    assert not target.exists()

    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    try:
        file_handler = main.configure_logging(log_dir=target, level="INFO")
        assert target.is_dir()  # created automatically
        assert (target / "trading-assistant.log").is_file()
        assert file_handler.baseFilename.endswith("trading-assistant.log")
    finally:
        for handler in list(root.handlers):
            if isinstance(handler, logging.StreamHandler):
                root.removeHandler(handler)
        for handler in saved_handlers:
            root.addHandler(handler)
