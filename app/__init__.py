"""Trading Assistant application package.

This package contains the core modules of the Trading Assistant:

- :mod:`app.config`        - environment/configuration loading
- :mod:`app.utils`         - small shared helpers (JSON, symbol, price)
- :mod:`app.plan_matcher`  - loads plans and matches incoming signals to them
- :mod:`app.notifier`      - Telegram / Notion / Obsidian notifications
- :mod:`app.mt5_handler`   - MetaTrader 5 order execution wrapper
- :mod:`app.server`        - Flask webhook + REST API server

The modules are intentionally decoupled: the matcher and notifier contain no
MT5 or network dependency at import time, which keeps them unit-testable on any
operating system.
"""

__all__ = [
    "config",
    "utils",
    "plan_matcher",
    "notifier",
    "mt5_handler",
    "server",
]

__version__ = "1.0.0"
