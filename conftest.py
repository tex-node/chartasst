"""Pytest bootstrap.

Ensures the project root is importable as ``app`` regardless of the working
directory pytest is invoked from.
"""

import pathlib
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def _reset_runtime_singletons(monkeypatch):
    """Keep runtime singletons isolated and give tests a deterministic baseline.

    The baseline overrides any values loaded from a developer's local ``.env``
    (e.g. ``LIVE_TRADING=false``, SMTP or Telegram credentials) so the suite is
    hermetic. Individual tests override these as needed.
    """
    from app import runtime
    from app.config import Config

    monkeypatch.setattr(Config, "LIVE_TRADING", True)
    monkeypatch.setattr(Config, "SMTP_HOST", "")
    monkeypatch.setattr(Config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(Config, "TELEGRAM_CHAT_ID", "")

    runtime.flags.reset()
    runtime.uptime.reset()
    runtime.trigger_log.reset()
    yield
    runtime.flags.reset()
    runtime.uptime.reset()
    runtime.trigger_log.reset()
