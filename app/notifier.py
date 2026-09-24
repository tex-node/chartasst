"""User notifications and trade journaling.

Three independent side channels are supported:

* **Telegram** - real-time mobile alerts (always attempted when configured).
* **Notion**   - a page per trade in a Notion database (optional).
* **Obsidian** - a Markdown entry in a daily note (optional).

Each channel is isolated: a failure in one *never* prevents the others, and no
channel failure is allowed to propagate into the trading path. This is a
deliberate design choice so a flaky journaling API can never block an order.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import requests

from app import runtime
from app.config import Config
from app.emailer import Emailer
from app.utils import utc_date_str, utc_now_iso, utc_time_str

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
TRADE_LOG_DIRNAME = "Trade_Logs"


def _escape_md(text) -> str:
    """Escape characters that Telegram's legacy Markdown parser treats as syntax."""
    value = "" if text is None else str(text)
    for ch in ("_", "*", "`", "["):
        value = value.replace(ch, "\\" + ch)
    # Keep messages single-paragraph friendly.
    return value.replace("\r", "").strip()


def _action_emoji(action: str) -> str:
    """Return 🟢 for buy-like actions and 🔴 for sell-like actions."""
    return "🟢" if str(action).strip().lower() in ("buy", "long") else "🔴"


class Notifier:
    """Sends alerts to Telegram and journals trades to Notion/Obsidian."""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.emailer = Emailer()

    # ------------------------------------------------------------------ #
    # Message formatting (pure functions - easy to unit test)
    # ------------------------------------------------------------------ #
    def _format_signal_message(self, signal: dict, plan: dict, result: dict) -> str:
        """Build the Markdown body for a matched/executed signal alert."""
        signal = signal or {}
        plan = plan or {}
        result = result or {}

        emoji = _action_emoji(plan.get("action", signal.get("action", "")))
        action = str(plan.get("action", signal.get("action", ""))).upper()
        symbol = plan.get("symbol") or signal.get("symbol", "?")
        name = plan.get("name") or "Unnamed plan"

        lines = [
            f"{emoji} *{action} SIGNAL* — {_escape_md(symbol)}",
            f"📋 Plan: {_escape_md(name)}",
            f"🎯 Action: {_escape_md(action)}",
        ]

        timeframe = plan.get("timeframe") or signal.get("timeframe")
        if timeframe:
            lines.append(f"⏱ Timeframe: {_escape_md(timeframe)}")

        condition = plan.get("condition") or signal.get("condition")
        if condition:
            lines.append(f"🧭 Condition: {_escape_md(condition)}")

        price = signal.get("price")
        if price is not None:
            lines.append(f"💲 Trigger price: {_escape_md(price)}")

        if result.get("success"):
            ticket = result.get("order")
            exec_price = result.get("price")
            sl = result.get("sl")
            tp = result.get("tp")
            exec_bits = []
            if ticket is not None:
                exec_bits.append(f"ticket #{ticket}")
            if exec_price is not None:
                exec_bits.append(f"@ {exec_price}")
            if sl is not None:
                exec_bits.append(f"SL {sl}")
            if tp is not None:
                exec_bits.append(f"TP {tp}")
            lines.append("⚙️ Execution: ✅ " + (", ".join(exec_bits) or "filled"))
        else:
            reason = result.get("error") or "not executed"
            lines.append(f"⚙️ Execution: ⚠️ {_escape_md(reason)}")
            if result.get("retcode_text"):
                lines.append(f"❌ Reason: {_escape_md(result['retcode_text'])}")

        notes = plan.get("notes")
        if notes:
            lines.append(f"📝 Notes: {_escape_md(notes)}")

        lines.append(f"🕒 {utc_now_iso()}")
        return "\n".join(lines)

    def _format_unplanned_message(self, signal: dict) -> str:
        """Build the Markdown body for a signal that matched no active plan."""
        signal = signal or {}
        symbol = signal.get("symbol", "?")
        action = str(signal.get("action", signal.get("direction", "?"))).upper()

        lines = [
            f"⚠️ *UNPLANNED SIGNAL* — {_escape_md(symbol)}",
            f"🎯 Action/Direction: {_escape_md(action)}",
        ]

        if signal.get("price") is not None:
            lines.append(f"💲 Price: {_escape_md(signal.get('price'))}")
        if signal.get("timeframe"):
            lines.append(f"⏱ Timeframe: {_escape_md(signal.get('timeframe'))}")
        if signal.get("object_name"):
            lines.append(f"📌 Object: {_escape_md(signal.get('object_name'))}")
        if signal.get("condition"):
            lines.append(f"🧭 Condition: {_escape_md(signal.get('condition'))}")

        lines.append("❗ No active plan matched — *no order was placed*.")
        lines.append(f"🕒 {utc_now_iso()}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    @staticmethod
    def _to_plain(text: str) -> str:
        """Strip the light Markdown escaping/formatting used for Telegram."""
        return (text or "").replace("\\", "").replace("*", "")

    def _send_email(self, subject: str, body: str) -> bool:
        """Send an email alert via :class:`app.emailer.Emailer`.

        Isolated: any failure is logged and swallowed so it can never affect the
        trading flow or the Telegram alert.
        """
        try:
            return self.emailer.send(subject, body)
        except Exception as exc:  # defensive: emailer already catches internally
            logger.error("Email notification failed ('%s'): %s", subject, exc)
            return False

    def send_signal_alert(self, signal: dict, plan: dict, result: dict) -> bool:
        """Send a matched-signal alert to Telegram and email, and journal it.

        Returns ``True`` when the Telegram message was delivered. Email and
        journaling errors never affect the return value.
        """
        message = self._format_signal_message(signal, plan, result)
        sent = self._send_telegram(message)
        self._log_to_notion(signal, plan, result)
        self._log_to_obsidian(signal, plan, result)

        signal = signal or {}
        plan = plan or {}
        result = result or {}
        symbol = plan.get("symbol") or signal.get("symbol", "?")
        action = str(plan.get("action", signal.get("action", ""))).upper()
        status = "EXECUTED" if result.get("success") else "NOT EXECUTED"
        self._send_email(
            f"[Trading Assistant] {status}: {symbol} {action}".strip(),
            self._to_plain(message),
        )
        return sent

    def send_unplanned_alert(self, signal: dict) -> bool:
        """Alert the user (Telegram + email) about a signal with no active plan."""
        message = self._format_unplanned_message(signal)
        sent = self._send_telegram(message)
        symbol = (signal or {}).get("symbol", "?")
        self._send_email(
            f"[Trading Assistant] Unplanned signal: {symbol}",
            self._to_plain(message),
        )
        return sent

    def _format_startup_message(
        self, mt5_connected: bool, plan_count: int, live_trading: bool
    ) -> str:
        """Build the startup ping body (UTC timestamp, MT5 state, plans, mode)."""
        mt5_state = "connected ✅" if mt5_connected else "NOT connected ❌"
        mode = "ON 🟢" if live_trading else "OFF (alert-only) 🔴"
        return "\n".join(
            [
                "🚀 *Trading Assistant started*",
                f"🕒 {utc_now_iso()}",
                f"🔌 MT5: {mt5_state}",
                f"📋 Active plans loaded: {plan_count}",
                f"⚙️ Live trading: {mode}",
            ]
        )

    def send_startup_message(
        self, mt5_connected: bool, plan_count: int, live_trading: bool
    ) -> bool:
        """Send a one-line "I'm alive" Telegram ping on startup.

        Tells you the bot came back (e.g. after an NSSM restart or VPS reboot),
        whether MT5 is connected, how many plans are loaded and whether trading
        is live. No-ops (returns ``False``) when Telegram is not configured.
        """
        message = self._format_startup_message(mt5_connected, plan_count, live_trading)
        sent = self._send_telegram(message)
        self._send_email("[Trading Assistant] Started", self._to_plain(message))
        return sent

    # ------------------------------------------------------------------ #
    # Telegram
    # ------------------------------------------------------------------ #
    def send_message(self, text: str, chat_id: str | None = None) -> bool:
        """Send a Markdown message to a specific chat (defaults to configured).

        Never raises. Used by the Telegram command listener to reply.
        """
        target = chat_id or Config.TELEGRAM_CHAT_ID
        if not Config.TELEGRAM_BOT_TOKEN or not target:
            logger.warning("Telegram not configured; skipping message.")
            return False

        url = f"{TELEGRAM_API_BASE}/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": target,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        try:
            response = self.session.post(url, json=payload, timeout=10)
            if response.status_code == 200 and response.json().get("ok"):
                logger.info("Telegram message sent to %s.", target)
                return True
            logger.error(
                "Telegram send_message error: HTTP %s - %s",
                response.status_code,
                response.text[:300],
            )
            return False
        except requests.RequestException as exc:
            logger.error("Telegram send_message failed: %s", exc)
            return False
        except ValueError as exc:
            logger.error("Telegram send_message response was not JSON: %s", exc)
            return False

    def _send_telegram(self, message: str) -> bool:
        """Low-level Telegram ``sendMessage`` call. Returns success flag.

        Never raises: any network/API error is logged and reported as ``False``.
        """
        if not Config.is_telegram_enabled():
            logger.warning("Telegram not configured; skipping message.")
            return False

        url = f"{TELEGRAM_API_BASE}/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": Config.TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        try:
            response = self.session.post(url, json=payload, timeout=10)
            if response.status_code == 200 and response.json().get("ok"):
                logger.info("Telegram alert sent (%d chars).", len(message))
                return True
            logger.error(
                "Telegram API error: HTTP %s - %s",
                response.status_code,
                response.text[:500],
            )
            return False
        except requests.RequestException as exc:
            logger.error("Telegram request failed: %s", exc)
            return False
        except ValueError as exc:  # JSON decode failure
            logger.error("Telegram response was not valid JSON: %s", exc)
            return False

    # ------------------------------------------------------------------ #
    # Notion
    # ------------------------------------------------------------------ #
    def _log_to_notion(self, signal: dict, plan: dict, result: dict) -> None:
        """Create a Notion page for the trade (no-op when not configured).

        The page title uses the database's own title property (discovered at
        runtime), and the full trade details are written as page content, so
        this works with any database schema.
        """
        if not Config.is_notion_enabled():
            logger.debug("Notion not configured; skipping.")
            return

        try:
            from notion_client import Client  # imported lazily on purpose

            client = Client(auth=Config.NOTION_API_KEY)
            signal = signal or {}
            plan = plan or {}
            result = result or {}

            title = (
                f"{plan.get('symbol') or signal.get('symbol', '?')} "
                f"{str(plan.get('action', signal.get('action', ''))).upper()} "
                f"- {plan.get('name', 'Unplanned')}"
            )

            # Discover the database's title property name.
            title_prop = "Name"
            try:
                db = client.databases.retrieve(database_id=Config.NOTION_DATABASE_ID)
                for prop_name, meta in (db.get("properties") or {}).items():
                    if meta.get("type") == "title":
                        title_prop = prop_name
                        break
            except Exception as exc:  # pragma: no cover - network dependent
                logger.warning("Could not read Notion database schema: %s", exc)

            detail_lines = [
                f"Status: {'EXECUTED' if result.get('success') else 'NOT EXECUTED'}",
                f"Symbol: {plan.get('symbol') or signal.get('symbol', '?')}",
                f"Action: {plan.get('action', signal.get('action', ''))}",
                f"Condition: {plan.get('condition', signal.get('condition', ''))}",
                f"Trigger price: {signal.get('price', '')}",
                f"Ticket: {result.get('order', '')}",
                f"Fill price: {result.get('price', '')}",
                f"SL: {result.get('sl', '')}",
                f"TP: {result.get('tp', '')}",
                f"Error: {result.get('error', '')}",
                f"Error detail: {result.get('retcode_text', '')}",
                f"Notes: {plan.get('notes', '')}",
                f"Logged at: {utc_now_iso()} (UTC)",
            ]

            client.pages.create(
                parent={"database_id": Config.NOTION_DATABASE_ID},
                properties={
                    title_prop: {"title": [{"text": {"content": title}}]},
                },
                children=[
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [
                                {"type": "text", "text": {"content": "\n".join(detail_lines)}}
                            ]
                        },
                    }
                ],
            )
            logger.info("Trade logged to Notion: %s", title)
        except Exception as exc:
            logger.error("Notion logging failed: %s", exc)

    # ------------------------------------------------------------------ #
    # Obsidian
    # ------------------------------------------------------------------ #
    def _log_to_obsidian(self, signal: dict, plan: dict, result: dict) -> None:
        """Append a Markdown trade entry to the Obsidian daily note.

        The filename and timestamps use **UTC** so the whole journal shares a
        single clock (independent of the VPS or broker timezone). No-ops when
        ``OBSIDIAN_VAULT_PATH`` is not configured. Any file error is logged and
        swallowed.
        """
        if not Config.is_obsidian_enabled():
            logger.debug("Obsidian not configured; skipping.")
            return

        try:
            signal = signal or {}
            plan = plan or {}
            result = result or {}

            log_dir = Path(Config.OBSIDIAN_VAULT_PATH) / TRADE_LOG_DIRNAME
            log_dir.mkdir(parents=True, exist_ok=True)
            note_path = log_dir / f"{utc_date_str()}.md"

            symbol = plan.get("symbol") or signal.get("symbol", "?")
            action = str(plan.get("action", signal.get("action", ""))).upper()
            status = "EXECUTED" if result.get("success") else "NOT EXECUTED"

            entry_lines = [
                "",
                f"## {utc_time_str()} UTC — {symbol} {action}",
                f"- **Plan:** {plan.get('name', 'Unplanned')}",
                f"- **Status:** {status}",
                f"- **Error detail:** {result.get('retcode_text', '')}",
                f"- **Condition:** {plan.get('condition', signal.get('condition', ''))}",
                f"- **Trigger price:** {signal.get('price', '')}",
                f"- **Ticket:** {result.get('order', '')}",
                f"- **Fill price:** {result.get('price', '')}",
                f"- **SL/TP:** {result.get('sl', '')} / {result.get('tp', '')}",
                f"- **Notes:** {plan.get('notes', '')}",
                f"- **Logged at:** {utc_now_iso()}",
                "",
            ]

            with note_path.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(entry_lines))
            logger.info("Trade logged to Obsidian: %s", note_path)
        except Exception as exc:
            logger.error("Obsidian logging failed: %s", exc)


class TelegramCommander:
    """Turns the Telegram bot into a control surface via long-polling.

    Handles ``/help``, ``/status``, ``/plans``, ``/stale``, ``/panic``,
    ``/resume`` and ``/close <ticket>``. Only commands from the configured
    ``TELEGRAM_CHAT_ID`` are acted upon.

    Every action that touches MT5 goes through the ``MT5Handler``, which itself
    enforces ``LIVE_TRADING`` / the runtime kill switch - so no command can
    place or close an order while trading is disabled.
    """

    def __init__(self, matcher, handler, notifier=None) -> None:
        self.matcher = matcher
        self.handler = handler
        self.notifier = notifier if notifier is not None else Notifier()

    # --- authorization -------------------------------------------------
    @staticmethod
    def _authorized(chat_id) -> bool:
        """Only the configured chat id may issue commands."""
        configured = str(Config.TELEGRAM_CHAT_ID or "").strip()
        if not configured:
            return False
        return str(chat_id).strip() == configured

    # --- command dispatch (pure; easy to unit test) --------------------
    def handle_command(self, text: str, chat_id) -> str:
        """Return the reply for a command, or ``""`` to stay silent.

        An empty reply means "not authorized" or "not a command", so the
        listener sends nothing.
        """
        if not self._authorized(chat_id):
            logger.warning("Ignoring Telegram command from unauthorized chat %s", chat_id)
            return ""

        text = (text or "").strip()
        if not text.startswith("/"):
            return ""
        parts = text.split()
        command = parts[0].lower().split("@")[0]  # strip @botname suffix
        args = parts[1:]
        try:
            if command == "/help":
                return self._help()
            if command == "/status":
                return self._status()
            if command == "/plans":
                return self._plans()
            if command == "/stale":
                return self._stale()
            if command == "/panic":
                return self._panic()
            if command == "/resume":
                return self._resume()
            if command == "/close":
                return self._close(args)
        except Exception as exc:  # never let a command crash the listener
            logger.exception("Telegram command %s failed: %s", command, exc)
            return f"⚠️ Command failed: {_escape_md(exc)}"
        return f"Unknown command: {_escape_md(command)}. Send /help."

    # --- individual commands -------------------------------------------
    def _help(self) -> str:
        return "\n".join(
            [
                "🤖 *Trading Assistant commands*",
                "/status — MT5 state, live-trading state, open positions",
                "/plans — list active plans",
                "/stale — list plans active > 30 days",
                "/panic — disable trading immediately (runtime)",
                "/resume — re-enable trading (runtime)",
                "/close <ticket> — close a position",
                "/help — this message",
            ]
        )

    def _status(self) -> str:
        try:
            connected = bool(self.handler.is_connected())
        except Exception:
            connected = False
        lines = [
            "📟 *Status*",
            f"🕒 {utc_now_iso()}",
            f"🔌 MT5: {'connected' if connected else 'NOT connected'}",
            f"⚙️ Live trading: {'ON' if runtime.flags.live_trading_effective() else 'OFF'}",
            f"🚨 Kill switch: {'ENGAGED' if runtime.flags.is_trading_forced_off() else 'off'}",
        ]
        try:
            positions = self.handler.get_positions() or {}
            lines.append(f"📈 Open positions: {positions.get('count', 0)}")
        except Exception:
            pass
        return "\n".join(lines)

    def _plans(self) -> str:
        plans = self.matcher.get_active_plans()
        if not plans:
            return "📋 No active plans."
        lines = [f"📋 *Active plans ({len(plans)})*"]
        for plan in plans:
            lines.append(
                f"• `{plan.get('id')}` {_escape_md(plan.get('symbol'))} "
                f"{str(plan.get('action', '')).upper()} — "
                f"{_escape_md(plan.get('object_name') or plan.get('name'))}"
            )
        return "\n".join(lines)

    def _stale(self) -> str:
        stale = (
            self.matcher.get_stale_plans()
            if hasattr(self.matcher, "get_stale_plans")
            else []
        )
        if not stale:
            return "🗂 No stale plans. Nice."
        lines = [f"🗂 *Stale plans ({len(stale)})* — active > 30 days, review or retire:"]
        for plan in stale:
            lines.append(
                f"• `{plan.get('id')}` {_escape_md(plan.get('symbol'))} "
                f"{str(plan.get('action', '')).upper()}"
            )
        return "\n".join(lines)

    def _panic(self) -> str:
        runtime.flags.force_disable_trading("telegram /panic")
        logger.warning("Kill switch engaged via Telegram /panic.")
        return "🚨 *PANIC* — trading disabled at runtime. Send /resume to re-enable."

    def _resume(self) -> str:
        runtime.flags.resume_trading()
        logger.warning("Kill switch cleared via Telegram /resume.")
        if not runtime.flags.live_trading_effective():
            return (
                "⚙️ Kill switch cleared, but LIVE_TRADING=false in .env — "
                "still alert-only."
            )
        return "⚙️ *Resumed* — live trading is ON."

    def _close(self, args) -> str:
        if not args or not str(args[0]).isdigit():
            return "Usage: /close <ticket>"
        ticket = int(args[0])
        if not runtime.flags.live_trading_effective():
            return "🔒 Trading is disabled (kill switch or .env) — close blocked."
        result = self.handler.close_position(ticket)
        if result.get("success"):
            return f"✅ Closed position {ticket}."
        return f"⚠️ Could not close {ticket}: {_escape_md(result.get('error'))}"

    # --- long polling ---------------------------------------------------
    def _api_url(self, method: str) -> str:
        return f"{TELEGRAM_API_BASE}/bot{Config.TELEGRAM_BOT_TOKEN}/{method}"

    def get_updates(self, offset=None, timeout: int = 25) -> list:
        """Fetch pending updates via Telegram ``getUpdates``. Never raises."""
        if not Config.is_telegram_enabled():
            return []
        params: dict = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        try:
            response = self.notifier.session.get(
                self._api_url("getUpdates"), params=params, timeout=timeout + 10
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("ok"):
                    return data.get("result", []) or []
            logger.error("getUpdates returned HTTP %s", response.status_code)
        except (requests.RequestException, ValueError) as exc:
            logger.error("getUpdates failed: %s", exc)
        return []

    def run_forever(self, poll_interval: float = 2.0, stop_event=None) -> None:
        """Poll Telegram and reply to commands until ``stop_event`` is set."""
        if not Config.is_telegram_enabled():
            logger.warning("Telegram not configured; command listener disabled.")
            return
        logger.info("Telegram command listener started.")
        offset = None
        while not (stop_event is not None and stop_event.is_set()):
            for update in self.get_updates(offset=offset):
                offset = (update.get("update_id") or 0) + 1
                message = update.get("message") or update.get("edited_message") or {}
                chat_id = (message.get("chat") or {}).get("id")
                text = message.get("text", "")
                reply = self.handle_command(text, chat_id)
                if reply:
                    self.notifier.send_message(reply)
            if stop_event is not None:
                if stop_event.wait(poll_interval):
                    break
            else:
                time.sleep(poll_interval)
        logger.info("Telegram command listener stopped.")
