"""Application configuration.

All settings are read from environment variables, optionally loaded from a
local ``.env`` file via :mod:`python-dotenv`. The settings are exposed as
attributes on the :class:`Config` class so that any module can simply do::

    from app.config import Config

    print(Config.FLASK_PORT)

Call :meth:`Config.load` to (re)read the environment and :meth:`Config.validate`
to obtain a list of human-readable problems. ``main.py`` calls both on startup
and refuses to boot when a *critical* value is missing.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Project root is the parent of the "app" package directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"

# Variables that must be present for the system to be able to trade at all.
CRITICAL_VARS = ("API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")


def _as_int(value, default: int) -> int:
    """Best-effort conversion of ``value`` to ``int``, falling back to default."""
    if value is None or str(value).strip() == "":
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        logger.warning("Could not parse %r as int; using default %s", value, default)
        return default


def _as_float(value, default: float) -> float:
    """Best-effort conversion of ``value`` to ``float``, falling back to default."""
    if value is None or str(value).strip() == "":
        return default
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        logger.warning("Could not parse %r as float; using default %s", value, default)
        return default


def _as_bool(value, default: bool) -> bool:
    """Interpret common truthy/falsey strings as a boolean."""
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on", "y")


def _as_str(value, default: str = "") -> str:
    """Return a stripped string, or ``default`` when the value is empty."""
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


class Config:
    """Container for all runtime configuration values.

    Attributes are populated by :meth:`load`. Defaults are safe for local
    development: trading is enabled, but every credential placeholder is empty
    so :meth:`validate` reports clear errors.
    """

    # --- MetaTrader 5 -----------------------------------------------------
    MT5_LOGIN: int = 0
    MT5_PASSWORD: str = ""
    MT5_SERVER: str = ""
    MT5_PATH: str = ""
    MT5_DEFAULT_SUFFIX: str = ""

    # --- Telegram ---------------------------------------------------------
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # --- Notion -----------------------------------------------------------
    NOTION_API_KEY: str = ""
    NOTION_DATABASE_ID: str = ""

    # --- Obsidian ---------------------------------------------------------
    OBSIDIAN_VAULT_PATH: str = ""

    # --- Email (SMTP) -----------------------------------------------------
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    SMTP_USE_TLS: bool = True
    SMTP_USE_SSL: bool = False
    NOTIFY_EMAIL_TO: str = "nodeswavemonitor@gmail.com"

    # --- Server -----------------------------------------------------------
    FLASK_PORT: int = 5000
    API_KEY: str = "change_me_to_a_long_random_secret"

    # --- Trading defaults -------------------------------------------------
    DEFAULT_VOLUME: float = 0.01
    DEFAULT_STOP_LOSS: int = 200
    DEFAULT_TAKE_PROFIT: int = 400

    # --- Pre-flight risk limits ------------------------------------------
    # Reject an order before sending when it breaks any of these sanity checks.
    MAX_VOLUME: float = 1.0            # maximum lots per order
    MIN_SL_POINTS: int = 50            # minimum stop-loss distance in points
    MAX_RISK_PER_TRADE: float = 50.0   # maximum loss at SL in account currency

    # --- Risk / misc ------------------------------------------------------
    LIVE_TRADING: bool = True
    LOG_LEVEL: str = "INFO"

    _loaded: bool = False

    # ------------------------------------------------------------------ #
    # Loading / validation
    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls, env_file: str | os.PathLike | None = None) -> "Config":
        """Load configuration from the environment.

        Parameters
        ----------
        env_file:
            Optional path to a ``.env`` file. When omitted the default
            ``<project_root>/.env`` is used if it exists.

        Returns
        -------
        Config
            The class itself, allowing ``Config.load()`` chaining.
        """
        env_path = Path(env_file) if env_file else DEFAULT_ENV_FILE
        if env_path.exists():
            load_dotenv(dotenv_path=env_path, override=False)
            logger.debug("Loaded environment from %s", env_path)
        else:
            # Still honour variables already present in the process environment.
            load_dotenv(override=False)
            logger.debug("No .env file at %s; using process environment", env_path)

        get = os.environ.get

        cls.MT5_LOGIN = _as_int(get("MT5_LOGIN"), 0)
        cls.MT5_PASSWORD = _as_str(get("MT5_PASSWORD"))
        cls.MT5_SERVER = _as_str(get("MT5_SERVER"))
        cls.MT5_PATH = _as_str(get("MT5_PATH"))
        cls.MT5_DEFAULT_SUFFIX = _as_str(get("MT5_DEFAULT_SUFFIX"))

        cls.TELEGRAM_BOT_TOKEN = _as_str(get("TELEGRAM_BOT_TOKEN"))
        cls.TELEGRAM_CHAT_ID = _as_str(get("TELEGRAM_CHAT_ID"))

        cls.NOTION_API_KEY = _as_str(get("NOTION_API_KEY"))
        cls.NOTION_DATABASE_ID = _as_str(get("NOTION_DATABASE_ID"))

        cls.OBSIDIAN_VAULT_PATH = _as_str(get("OBSIDIAN_VAULT_PATH"))

        cls.SMTP_HOST = _as_str(get("SMTP_HOST"))
        cls.SMTP_PORT = _as_int(get("SMTP_PORT"), 587)
        cls.SMTP_USERNAME = _as_str(get("SMTP_USERNAME"))
        cls.SMTP_PASSWORD = _as_str(get("SMTP_PASSWORD"))
        cls.SMTP_FROM = _as_str(get("SMTP_FROM"))
        cls.SMTP_USE_TLS = _as_bool(get("SMTP_USE_TLS"), True)
        cls.SMTP_USE_SSL = _as_bool(get("SMTP_USE_SSL"), False)
        cls.NOTIFY_EMAIL_TO = _as_str(get("NOTIFY_EMAIL_TO"), "nodeswavemonitor@gmail.com")

        cls.FLASK_PORT = _as_int(get("FLASK_PORT"), 5000)
        cls.API_KEY = _as_str(get("API_KEY"), "change_me_to_a_long_random_secret")

        cls.DEFAULT_VOLUME = _as_float(get("DEFAULT_VOLUME"), 0.01)
        cls.DEFAULT_STOP_LOSS = _as_int(get("DEFAULT_STOP_LOSS"), 200)
        cls.DEFAULT_TAKE_PROFIT = _as_int(get("DEFAULT_TAKE_PROFIT"), 400)

        cls.MAX_VOLUME = _as_float(get("MAX_VOLUME"), 1.0)
        cls.MIN_SL_POINTS = _as_int(get("MIN_SL_POINTS"), 50)
        cls.MAX_RISK_PER_TRADE = _as_float(get("MAX_RISK_PER_TRADE"), 50.0)

        cls.LIVE_TRADING = _as_bool(get("LIVE_TRADING"), True)
        cls.LOG_LEVEL = _as_str(get("LOG_LEVEL"), "INFO").upper()

        cls._loaded = True
        return cls

    @classmethod
    def validate(cls, require_mt5: bool = True) -> list[str]:
        """Return a list of configuration problems (empty == all good).

        Parameters
        ----------
        require_mt5:
            When ``True`` (the default) the MT5 credentials are treated as
            required. Tests and alert-only deployments can pass ``False``.
        """
        problems: list[str] = []

        if not cls.TELEGRAM_BOT_TOKEN:
            problems.append("TELEGRAM_BOT_TOKEN is not set - notifications disabled.")
        if not cls.TELEGRAM_CHAT_ID:
            problems.append("TELEGRAM_CHAT_ID is not set - notifications disabled.")
        if not cls.API_KEY or cls.API_KEY == "change_me_to_a_long_random_secret":
            problems.append("API_KEY is missing or still the placeholder value.")

        if require_mt5:
            if not cls.MT5_LOGIN:
                problems.append("MT5_LOGIN is not set - cannot log in to MT5.")
            if not cls.MT5_PASSWORD:
                problems.append("MT5_PASSWORD is not set - cannot log in to MT5.")
            if not cls.MT5_SERVER:
                problems.append("MT5_SERVER is not set - cannot log in to MT5.")

        if cls.NOTION_API_KEY and not cls.NOTION_DATABASE_ID:
            problems.append("NOTION_API_KEY set but NOTION_DATABASE_ID missing.")
        if cls.NOTION_DATABASE_ID and not cls.NOTION_API_KEY:
            problems.append("NOTION_DATABASE_ID set but NOTION_API_KEY missing.")

        if cls.SMTP_HOST and not (cls.SMTP_FROM or cls.SMTP_USERNAME):
            problems.append("SMTP_HOST set but no SMTP_FROM or SMTP_USERNAME (sender).")
        if cls.SMTP_USE_SSL and cls.SMTP_USE_TLS:
            problems.append("SMTP_USE_SSL and SMTP_USE_TLS are both true; set only one.")

        if cls.DEFAULT_VOLUME <= 0:
            problems.append("DEFAULT_VOLUME must be greater than 0.")

        for problem in problems:
            logger.warning("Config: %s", problem)
        return problems

    @classmethod
    def is_notion_enabled(cls) -> bool:
        """True when both Notion credentials are configured."""
        return bool(cls.NOTION_API_KEY and cls.NOTION_DATABASE_ID)

    @classmethod
    def is_obsidian_enabled(cls) -> bool:
        """True when an Obsidian vault path is configured."""
        return bool(cls.OBSIDIAN_VAULT_PATH)

    @classmethod
    def is_email_enabled(cls) -> bool:
        """True when an SMTP host and a recipient address are configured."""
        return bool(cls.SMTP_HOST and cls.NOTIFY_EMAIL_TO)

    @classmethod
    def is_telegram_enabled(cls) -> bool:
        """True when both Telegram credentials are configured."""
        return bool(cls.TELEGRAM_BOT_TOKEN and cls.TELEGRAM_CHAT_ID)


# Populate the class attributes as soon as this module is imported. This keeps
# ``from app.config import Config; Config.FLASK_PORT`` working without an
# explicit load() call, while still allowing tests to call load() again.
Config.load()
