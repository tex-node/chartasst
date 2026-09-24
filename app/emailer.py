"""SMTP email notifications (standard library only).

A thin, dependency-free wrapper around :mod:`smtplib` used by the notifier and
the operational scripts. Email is fully optional: when ``SMTP_HOST`` is unset,
:meth:`Emailer.send` is a no-op and the rest of the system behaves exactly as
before.

Configuration (see ``.env``):

- ``SMTP_HOST``        - e.g. ``smtp.gmail.com``
- ``SMTP_PORT``        - default ``587`` (STARTTLS) or ``465`` (SSL)
- ``SMTP_USERNAME``    - SMTP login
- ``SMTP_PASSWORD``    - SMTP password / app password
- ``SMTP_FROM``        - From address (defaults to ``SMTP_USERNAME``)
- ``SMTP_USE_TLS``     - STARTTLS, default ``true``
- ``SMTP_USE_SSL``     - implicit SSL (for port 465), default ``false``
- ``NOTIFY_EMAIL_TO``  - recipient, defaults to ``nodeswavemonitor@gmail.com``
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from app.config import Config

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15


class Emailer:
    """Sends plain-text email via SMTP. Never raises."""

    def __init__(self, config=Config) -> None:
        self._config = config

    def is_configured(self) -> bool:
        """True when an SMTP host and recipient are configured."""
        cfg = self._config
        return bool(cfg.SMTP_HOST and cfg.NOTIFY_EMAIL_TO)

    def _from_address(self) -> str:
        """Best-effort From address."""
        cfg = self._config
        return cfg.SMTP_FROM or cfg.SMTP_USERNAME or "trading-assistant@localhost"

    def send(self, subject: str, body: str) -> bool:
        """Send an email. Returns ``True`` on success; logs and returns ``False``
        on any failure (missing config, network, auth)."""
        if not self.is_configured():
            logger.debug("Email not configured; skipping '%s'.", subject)
            return False

        cfg = self._config
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = formataddr(("Trading Assistant", self._from_address()))
        message["To"] = cfg.NOTIFY_EMAIL_TO
        message.set_content(body or "")

        server = None
        try:
            if cfg.SMTP_USE_SSL:
                server = smtplib.SMTP_SSL(cfg.SMTP_HOST, cfg.SMTP_PORT, timeout=DEFAULT_TIMEOUT)
            else:
                server = smtplib.SMTP(cfg.SMTP_HOST, cfg.SMTP_PORT, timeout=DEFAULT_TIMEOUT)
            server.ehlo()
            if cfg.SMTP_USE_TLS and not cfg.SMTP_USE_SSL:
                server.starttls()
                server.ehlo()
            if cfg.SMTP_USERNAME:
                server.login(cfg.SMTP_USERNAME, cfg.SMTP_PASSWORD)
            server.send_message(message)
            logger.info("Email sent to %s: %s", cfg.NOTIFY_EMAIL_TO, subject)
            return True
        except Exception as exc:  # never let email break a trading flow
            logger.error("Email send failed ('%s'): %s", subject, exc)
            return False
        finally:
            if server is not None:
                try:
                    server.quit()
                except Exception:
                    pass


# Module-level default instance and convenience helper.
emailer = Emailer()


def send_email(subject: str, body: str) -> bool:
    """Send an email using the default :class:`Emailer`. Never raises."""
    return emailer.send(subject, body)
