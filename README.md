# Trading Assistant

A self-hosted trading assistant that watches your pre-defined trading scenarios
on **TradingView** and **MetaTrader 5 (MT5)**, matches triggered signals against
the trading plan you wrote *in advance*, optionally executes the order through
the MT5 Python API, pings you on **Telegram**, and journals every trade to
**Notion** or **Obsidian** so you can review it from your phone.

The core idea is discipline: you analyse the charts and write down your
scenarios ("if price breaks above 1.0850 I buy with SL at 1.0830 and TP at
1.0870"). The assistant then does the boring part 24/7 — watching the levels,
firing only when reality matches your written plan, executing exactly what you
planned, and recording it — so you never have to chase the chart or second-guess
yourself in the heat of the moment.

The system is deliberately modular. The Flask webhook server, the plan matcher,
the MT5 execution handler and the notifier are separate pieces with clean
interfaces, so you can swap Telegram for something else, or Notion for Obsidian,
without touching the core logic. Everything runs as a single Python process on a
Windows VPS, and the only hard platform dependency is MT5 (Windows-only). Plans
are stored as plain JSON — there is no database to install or maintain.

> **Start on a demo account.** This software can place real market orders. Read
> the [Security notes](#security-notes) and use `LIVE_TRADING=false` (alert-only
> mode) until you are completely comfortable.

---

## Table of contents

- [Architecture](#architecture)
- [Project layout](#project-layout)
- [How a signal flows](#how-a-signal-flows)
- [Installation (Windows VPS)](#installation-windows-vps)
- [Configure TradingView alerts](#configure-tradingview-alerts)
- [MT5 setup and the MQL5 EA](#mt5-setup-and-the-mql5-ea)
- [Notion setup](#notion-setup)
- [Obsidian setup](#obsidian-setup)
- [Managing your plans](#managing-your-plans)
- [HTTP API reference](#http-api-reference)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Security notes](#security-notes)
- [Design decisions and assumptions](#design-decisions-and-assumptions)

---

## Architecture

```
┌─────────────────┐     Webhook      ┌──────────────────────┐
│  TradingView    │ ───────────────► │  Python Flask Server │
│  (Pine Alerts)  │                  │  (VPS: Windows)      │
└─────────────────┘                  │                      │
                                     │  ┌────────────────┐  │
┌─────────────────┐     Webhook      │  │ Decision Engine│  │
│  MT5 Terminal   │ ───────────────► │  │ (plan matcher) │  │
│  (MQL5 EA)      │                  │  └───────┬────────┘  │
└─────────────────┘                  │          │           │
                                     │  ┌───────▼────────┐  │
                                     │  │  MT5 Python API│  │
                                     │  │  (order_send)  │  │
                                     │  └───────┬────────┘  │
                                     │          │           │
                                     └──────────┼───────────┘
                                                │
                        ┌───────────┬─────────┼─────────┬───────────┐
                        │           │         │         │           │
               ┌────────▼──────┐ ┌──▼────┐ ┌──▼───────┐ ┌▼─────────┐
               │  Telegram Bot │ │ Email │ │ Notion   │ │ Obsidian │
               │               │ │(SMTP) │ │  API     │ │  vault   │
               └───────────────┘ └───────┘ └──────────┘ └──────────┘
```

Two independent sources feed the same decision engine:

1. **TradingView** Pine Script alerts POST JSON to `/webhook/tradingview`.
   These carry a `symbol` and an `action` (`buy`/`sell`).
2. **MT5** runs `ChartObjectMonitor.mq5`, an Expert Advisor that watches chart
   objects (horizontal lines, trendlines, rectangles) you drew in a specific
   colour and POSTs JSON to `/webhook/mt5` whenever price crosses or touches one.

The server matches the trigger against an **active plan** in `plans/plans.json`
by symbol + action (TradingView) or by chart-object name (MT5). On a match it
asks the MT5 handler to place the order and then notifies you. No match means an
"unplanned signal" alert and *no order*.

---

## Project layout

```
trading-assistant/                     (the repository root)
├── app/
│   ├── __init__.py
│   ├── config.py            # env/.env loading + validation
│   ├── server.py            # Flask webhook + REST API
│   ├── mt5_handler.py       # MetaTrader5 order execution wrapper
│   ├── plan_matcher.py      # plans.json load / match / update
│   ├── notifier.py          # Telegram + Notion + Obsidian
│   └── utils.py             # JSON / symbol / price helpers
├── mql5/
│   └── ChartObjectMonitor.mq5   # MT5 Expert Advisor
├── plans/
│   └── plans.json           # your pre-written trading plans
├── scripts/
│   ├── watchdog.py          # polls /health, alerts Telegram on failure
│   ├── daily_digest.py      # once-a-day Telegram summary
│   └── install_service.bat  # install as a Windows Service via NSSM
├── tests/
│   ├── test_plan_matcher.py
│   ├── test_server.py
│   ├── test_notifier.py
│   ├── test_mt5_handler.py
│   ├── test_main.py
│   └── test_watchdog.py
├── logs/                    # created at runtime (rotating logs)
├── .env.example
├── .gitignore
├── requirements.txt
├── pytest.ini
├── conftest.py
├── README.md
├── SETUP.md
├── start.bat                # Windows launcher (double-click to run)
└── main.py                  # entry point
```

---

## How a signal flows

```
TradingView alert ─┐
                   ├─► POST /webhook/...  ─► API-key check  ─► parse JSON
MT5 EA WebRequest ─┘                                              │
                                                                  ▼
                                            PlanMatcher.match / match_by_object
                                                                  │
                          ┌───────────────────────────────────────┴───────────┐
                          ▼                                                    ▼
                    matched (active plan)                             no match
                          │                                                    │
             MT5Handler.execute_plan (if LIVE_TRADING)            send_unplanned_alert
                          │                                                    │
              Notifier.send_signal_alert                           (no order placed)
                  ├── Telegram message
                  ├── Notion page (optional)
                  └── Obsidian daily note (optional)
```

---

## Installation (Windows VPS)

The full, hand-held version is in **[SETUP.md](SETUP.md)**. The short version:

1. **Install 64-bit Python 3.11 or 3.12** from python.org. *64-bit is
   mandatory* — the `MetaTrader5` package will not load in 32-bit Python.
2. **Install the MT5 terminal** and log into your (demo) account once manually.
3. **Get the code and create a virtual environment:**

   ```powershell
   git clone https://github.com/tex-node/chartasst.git
   cd chartasst
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

4. **Create your configuration file:**

   ```powershell
   copy .env.example .env
   notepad .env        # fill in MT5, Telegram, API_KEY, etc.
   ```

5. **Run the server.** The easiest way on Windows is the included launcher,
   which uses the virtual environment automatically:

   ```powershell
   .\start.bat
   ```

   Or run it directly:

   ```powershell
   .\.venv\Scripts\python.exe main.py
   ```

   `start.bat` passes any arguments through, e.g. `start.bat --telegram-listen`
   or `start.bat --dry-run`, and pauses at the end so you can read errors.

6. **Verify it is alive:**

   ```powershell
   curl http://127.0.0.1:5000/health
   # {"status":"healthy","mt5_connected":true}   (HTTP 200)
   ```

If `mt5_connected` is `false`, see [Troubleshooting](#troubleshooting).

### Keeping it running 24/7

Run the server under **Task Scheduler** (trigger: *At startup*; action:
`C:\chartasst\.venv\Scripts\python.exe main.py` with *Start in* `C:\chartasst`)
or as a **Windows Service** with a wrapper such as [NSSM](https://nssm.cc/).
Do **not** use Flask's reloader (the default in `main.py` is `use_reloader=False`
precisely so MT5 is not initialised twice).

---

## Configure TradingView alerts

TradingView can only reach your VPS if it is publicly accessible. For a quick,
secure setup use **Ngrok** (see SETUP.md for the install walkthrough). Assume
Ngrok gives you `https://your-name.ngrok-free.app`.

1. On the chart, create an alert (**Alt + A**).
2. Under **Condition**, choose your Pine indicator/strategy.
3. Under **Notifications**, tick **Webhook URL** and paste:

   ```
   https://your-name.ngrok-free.app/webhook/tradingview
   ```

4. In the **Message** box, paste a JSON body that includes `symbol` and `action`.
   Add `"X-API-Key"` via the **Webhook headers** field:

   ```json
   {
     "symbol": "EURUSD",
     "action": "buy",
     "price": {{close}},
     "timeframe": "H1",
     "condition": "Break above 1.0850"
   }
   ```

5. Add the header (TradingView alert dialog → **Webhook headers**, or include it
   in your own proxy):

   ```
   X-API-Key: <the value of API_KEY in your .env>
   ```

   > TradingView does not natively let you set arbitrary headers in all plan
   > tiers. If you cannot add headers, put Ngrok in front with its own auth, or
   > use a small Pine `request.security`-free approach where the API key is
   > embedded in the URL path via a reverse proxy. The simplest supported
   > approach is to send the key as a header **or** to enable Ngrok's basic-auth
   > (`ngrok http 5000 --basic-auth="user:pass"`). Never put the API key in the
   > JSON body.

The server accepts both `application/json` and `text/plain` bodies (TradingView
sends `text/plain` by default), so a raw JSON string in the Message box works.

---

## MT5 setup and the MQL5 EA

### 1. Allow the webhook URL in the terminal

In MT5: **Tools → Options → Expert Advisors**, tick **Allow WebRequest for
listed URL** and add:

```
http://127.0.0.1:5000
```

(Tick *Allow automated trading* too.) Without this, `WebRequest()` fails with
error **4060** and the EA logs a clear message telling you exactly this.

### 2. Compile the EA

1. Open **MetaEditor** (F4 inside MT5).
2. **File → Open** and select `mql5/ChartObjectMonitor.mq5` from this repo, or
   copy it into `MQL5/Experts/` in your terminal's data folder and open it there.
3. Press **F7** to compile. It should report `0 errors, 0 warnings`.

### 3. Attach it to a chart

1. In MT5, open the chart with the objects you want monitored (e.g. EURUSD H1).
2. Drag **ChartObjectMonitor** from the Navigator onto the chart.
3. In the inputs dialog set:
   - `InpWebhookURL` = `http://127.0.0.1:5000/webhook/mt5`
   - `InpAPIKey` = your `API_KEY`
   - `InpMonitorColor` = the colour you draw your levels in (default red)
   - `InpAlertCross` / `InpAlertTouch` / `InpCooldownSec` as desired.
4. Click **OK**; make sure the **Algo Trading** toolbar button is green.

Now draw a red **horizontal line** on the chart and name it to match a plan's
`object_name` (e.g. `Resistance_1.0850`). When price crosses it, the EA POSTs
the JSON payload and the server matches it against your plan.

The EA sends:

```json
{
  "event": "cross",
  "object_name": "Resistance_1.0850",
  "object_type": "horizontal_line",
  "price": 1.0850,
  "direction": "above",
  "symbol": "EURUSD",
  "timeframe": "PERIOD_H1"
}
```

---

## Notion setup

1. Go to <https://www.notion.so/my-integrations> → **New integration**. Give it
   a name and copy the **Internal Integration Secret** into `NOTION_API_KEY`.
2. Create (or open) the database you want to log trades into. It only needs a
   title property; the assistant discovers the title property name automatically
   and writes the trade details into the page body, so any schema works.
3. Open the database page → **••• → Connections → Connect to** your integration.
4. Copy the **database ID** from the database's URL. The URL looks like
   `https://www.notion.so/<workspace>/<DATABASE_ID>?v=...` — the 32-character
   string before `?v=` is the ID. Paste it into `NOTION_DATABASE_ID`.
5. Leave both values blank to disable Notion entirely.

---

## Obsidian setup

1. Set `OBSIDIAN_VAULT_PATH` to the local path of your vault, e.g.
   `C:\Users\You\Documents\MyVault`. A `Trade_Logs` folder is created
   automatically and one daily note per day (`YYYY-MM-DD.md`) is appended to.
2. Leave it blank to disable.

**To read it on your phone**, sync the vault folder with a mobile-friendly
service:
- **Obsidian Sync** (paid, first-party), or
- **iCloud / OneDrive / Google Drive / Dropbox** — put the vault inside a synced
  folder, and open it in the Obsidian mobile app on the same account.

Notion and Obsidian can both be enabled at once; the assistant writes to every
configured destination.

## Email notifications (SMTP)

Every notification is sent to **both Telegram and email** when email is
configured, so you have a second, independent channel (useful when Telegram is
down or you want a searchable inbox trail). Email is optional — leave
`SMTP_HOST` blank and the system behaves as before.

Recipient is `NOTIFY_EMAIL_TO` (default `nodeswavemonitor@gmail.com`).

### Gmail example (recommended)

Gmail requires an **App Password** (your normal password will not work, and 2FA
must be on):

1. Google Account → **Security → 2-Step Verification** (enable it).
2. Google Account → **Security → App passwords** → create one for "Mail".
   Copy the 16-character password.
3. Fill in `.env`:

   ```text
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USE_TLS=true
   SMTP_USE_SSL=false
   SMTP_USERNAME=your.sender@gmail.com
   SMTP_PASSWORD=your_16_char_app_password
   SMTP_FROM=your.sender@gmail.com
   NOTIFY_EMAIL_TO=nodeswavemonitor@gmail.com
   ```

4. Restart the server and trigger a test signal (see [Testing](#testing)).

For a provider using implicit SSL (usually port 465) set `SMTP_PORT=465`,
`SMTP_USE_SSL=true`, `SMTP_USE_TLS=false`.

### What gets emailed

| Event | Subject |
|-------|---------|
| Matched signal | `[Trading Assistant] EXECUTED: EURUSD BUY` (or `NOT EXECUTED`) |
| Unplanned signal | `[Trading Assistant] Unplanned signal: EURUSD` |
| Server startup | `[Trading Assistant] Started` |
| Daily digest | `[Trading Assistant] daily digest YYYY-MM-DD` |
| Watchdog alert | `[Trading Assistant] health check failing` |

Email failures are logged and swallowed — they never block a trade or a Telegram
alert. The SMTP password is a secret: keep it in `.env` (gitignored) and rotate
it like the others (see SETUP.md §13).

---

## Managing your plans

Edit `plans/plans.json` (or POST to the API — see below). A plan looks like:

```json
{
  "plans": [
    {
      "id": "plan_001",
      "name": "EURUSD H1 Resistance Breakout",
      "symbol": "EURUSD",
      "timeframe": "H1",
      "condition": "Price breaks above 1.0850 resistance",
      "object_name": "Resistance_1.0850",
      "action": "buy",
      "volume": 0.01,
      "sl_points": 200,
      "tp_points": 400,
      "status": "active",
      "notes": "Wait for H1 close above 1.0850",
      "created_at": "2026-09-01T08:00:00+00:00"
    }
  ]
}
```

| Field         | Meaning                                                                 |
|---------------|-------------------------------------------------------------------------|
| `id`          | Unique id; auto-assigned (`plan_NNN`) when omitted via the API.          |
| `created_at`  | ISO-8601 **UTC** creation time. Auto-filled by `add_plan()`. Active plans older than **30 days** are reported as `stale_plans` in `/health/full` so you remember to review or retire them. Plans without it are ignored for staleness. |
| `symbol`      | Base symbol **without** the broker suffix (`EURUSD`).                   |
| `action`      | `buy` or `sell`.                                                        |
| `object_name` | The MT5 chart-object name to match (`match_by_object`).                 |
| `volume`      | Lots; falls back to `DEFAULT_VOLUME`.                                   |
| `sl_points`   | Stop-loss distance in broker points; falls back to `DEFAULT_STOP_LOSS`. |
| `tp_points`   | Take-profit distance in broker points; falls back to `DEFAULT_TAKE_PROFIT`. |
| `status`      | Only `active` plans are matched. Use `disabled`/`expired`/`triggered`.  |

The matcher only ever fires **active** plans. Signals that do not match an
active plan trigger an *unplanned signal* alert and never place an order.

There is a small Python API for automation:

```python
from app.plan_matcher import PlanMatcher
pm = PlanMatcher()
pm.add_plan({"symbol": "AUDUSD", "action": "sell", "object_name": "AUD_Support"})
pm.update_plan_status("plan_001", "disabled")
```

---

## HTTP API reference

All endpoints except `/health` require the header `X-API-Key: <API_KEY>`.

| Method | Path                        | Description                                        |
|--------|-----------------------------|----------------------------------------------------|
| GET    | `/health`                   | `200` + `{"status":"healthy","mt5_connected":true}` when MT5 is connected, else `503` + `degraded`. |
| GET    | `/health/full`              | Detailed status: MT5 state, `live_trading_effective`, `panic`, `last_webhook_received`, `open_positions`, `stale_plans`, `uptime_seconds`, webhook counts. `200`/`503` like `/health`. |
| GET    | `/stats/today`              | Today's (UTC) operational stats for the digest: triggers, active/stale plans, MT5 uptime %, unauthorized count, retcode histogram. |
| POST   | `/panic`                    | **Kill switch.** Force trading off at runtime (no `.env` edit). Returns the previous effective state. |
| POST   | `/resume`                   | Clear the kill switch (won't re-enable if `.env` disables trading). |
| POST   | `/webhook/tradingview`      | Receive a TradingView alert. Returns `{"status":"received","matched":bool,"executed":bool}`. |
| POST   | `/webhook/mt5`              | Receive an MQL5 chart-object alert.                 |
| GET    | `/positions`                | List open MT5 positions.                            |
| POST   | `/close/<ticket>`           | Close a position by ticket.                         |

Status codes: `401` invalid/missing API key, `400` malformed payload, `500`
unexpected server error (returned as JSON, never a crash).

Example:

```powershell
# health (no key)
curl http://127.0.0.1:5000/health

# a fictional tradingview signal
curl -Method POST http://127.0.0.1:5000/webhook/tradingview `
  -Headers @{ "X-API-Key" = "your-api-key" } `
  -ContentType "application/json" `
  -Body '{"symbol":"EURUSD","action":"buy","price":1.0851}'

# positions
curl -Headers @{ "X-API-Key" = "your-api-key" } http://127.0.0.1:5000/positions
```

---

## Testing

The test suite mocks MT5 and all network calls, so it runs anywhere (it does not
need a terminal, an account, or internet access):

```powershell
.\.venv\Scripts\Activate.ps1
pytest -v
```

169 tests cover the plan matcher (matching, status filtering, persistence,
staleness), the notifier (message formatting, Telegram success/failure handling,
startup ping, Obsidian/Notion writes), the MT5 handler (order construction,
filling fallback, pre-flight risk limits, retcode histogram, the single
order-send choke point and kill-switch enforcement), the server (API-key auth,
payload validation, matched/unplanned flows, `/health`, `/health/full`,
`/stats/today`, `/panic`, `/resume`, positions, close), the runtime singletons
(kill switch, uptime, triggers), the Telegram command handler, the digest, the
watchdog and the CLI/logging setup.

The tests are the contract that the system behaves as specified — run them after
any change.

---

## Troubleshooting

### MT5 API errors

| Symptom | Likely cause / fix |
|---------|--------------------|
| `mt5.initialize()` fails, `last_error()` returns `(-10005, ...)` | Wrong `MT5_PATH` or terminal not running. Start `terminal64.exe` and/or fix the path. |
| `(-10006, ...)` / `Terminal: Authorization failed` | Wrong `MT5_LOGIN`/`MT5_PASSWORD`/`MT5_SERVER`. Log in manually once to confirm the exact server name. |
| `/health` returns `degraded` | The API is not logged in. Check the login/server values and that **Algo Trading** is enabled. |
| `Symbol not found` | Wrong symbol or missing `MT5_DEFAULT_SUFFIX`. Check Market Watch for the exact name (e.g. `EURUSD.r`). |
| Order rejected `retcode=10030` (`INVALID_FILL`) | Broker filling mode mismatch — the handler automatically retries IOC → FOK → RETURN. If all fail, the symbol may be closed. |
| Order rejected `retcode=10016` (`INVALID_STOPS`) | SL/TP too close to price for the broker's minimum stop distance. Increase `sl_points`/`tp_points`. |
| Order rejected `retcode=10019` (`NO_MONEY`) | Not enough free margin for that `volume`. Reduce volume or use a demo account. |
| `MetaTrader5 package is unavailable` at startup | Not on Windows, or 32-bit Python. Use 64-bit Windows Python. |
| Nothing executes, logs say "LIVE_TRADING is disabled" | `LIVE_TRADING=false` in `.env`. Set to `true` to place real orders. |

### MQL5 WebRequest failures

| Symptom | Fix |
|---------|-----|
| `WebRequest failed. LastError=4060` | The URL is not whitelisted. **Tools → Options → Expert Advisors → Allow WebRequest for listed URL**, add `http://127.0.0.1:5000`. |
| `HTTP 401` in the EA log | `InpAPIKey` does not match `API_KEY` in `.env`. |
| `HTTP 400` in the EA log | Payload missing `object_name`, or the JSON was malformed. |
| No alerts at all | The object colour does not equal `InpMonitorColor`, the object is not an HLINE/TREND/RECTANGLE, or the cooldown is still active. |
| `HTTP 500` | Check the server console; the full payload and stack trace are logged. |

### Telegram issues

| Symptom | Fix |
|---------|-----|
| No messages, log says "Telegram not configured" | `TELEGRAM_BOT_TOKEN` and/or `TELEGRAM_CHAT_ID` empty in `.env`. |
| Log shows `HTTP 400 ... chat not found` | Wrong `TELEGRAM_CHAT_ID`, or you have not sent the bot a `/start` message yet. |
| Log shows `HTTP 401 Unauthorized` | Bad bot token. Re-copy it from BotFather. |
| Log shows `HTTP 400 ... can't parse entities` | A payload contained Markdown special characters. The notifier escapes the common ones; if you add new fields, escape them. |

Any Telegram failure is logged and swallowed — it never blocks an order.

### General

- **Server will not start / port in use:** change `FLASK_PORT` in `.env`.
- **Duplicate orders:** check the logs for `Duplicate signal within 10s window`.
  The system logs duplicates but still processes them by design. To prevent
  re-firing, set the plan's `status` to `triggered`.
- **Config warnings on boot:** `main.py` prints every problem it found. Fix them
  in `.env`; they are warnings, but trading needs the credentials.
- **Webhooks silently stopped arriving:** check `/health/full` and compare
  `last_webhook_received` with the current time. If it is stale, your tunnel URL
  likely changed (free Ngrok) or the MT5 terminal disconnected. Use a static
  tunnel and the watchdog.
- **Timestamps / timezone:** the journal uses **UTC throughout** — Telegram
  alerts, the Notion page and the Obsidian daily note (both the filename and the
  entry header) all use UTC, so there is a single clock. The one exception is
  MT5's own position `time` field, which is in the **broker's server time**; when
  you cross-reference a raw MT5 position, note that it is not UTC. Set the VPS
  timezone deliberately (UTC is a good default) for consistent log files.

---

## Operational hardening

These features keep the system alive and observable without you watching it.

### Rotating logs

`main.py` logs to **stdout and** `logs/trading-assistant.log`, rotated at
**10 MB with 5 backups** (`trading-assistant.log.1` … `.5`). Nothing grows
unbounded when you run it as a service. Configure verbosity with `LOG_LEVEL`.

### Full health endpoint

`GET /health/full` (public, like `/health`) returns:

```json
{
  "status": "healthy",
  "mt5_connected": true,
  "last_webhook_received": "2026-09-24T15:50:04+00:00",
  "last_webhook_source": "tradingview",
  "webhook_count": 12,
  "last_authorized_webhook_received": "2026-09-24T15:50:04+00:00",
  "authorized_webhook_count": 11,
  "unauthorized_count": 1,
  "open_positions": 1,
  "stale_plans": 1,
  "stale_plan_ids": ["plan_007"],
  "uptime_seconds": 3600.5,
  "server_time_utc": "2026-09-24T15:50:04+00:00"
}
```

`last_webhook_received` is updated at the **top of every webhook handler**, so it
counts *every* inbound request — including ones that fail auth (`401`) or carry
malformed JSON (`400`). That is deliberate: a stale `last_webhook_received`
therefore means *"nothing is reaching the server at all"* (a broken tunnel or a
down sender), not *"a webhook arrived but was rejected"*. Use
`last_authorized_webhook_received` / `authorized_webhook_count` to distinguish
the authenticated subset, and `unauthorized_count` to spot auth failures.

### Startup notification

On boot the server sends a one-line Telegram ping (suppressed when
`TELEGRAM_BOT_TOKEN` is unset) so an NSSM restart or VPS reboot is visible:

```
🚀 Trading Assistant started
🕒 2026-09-24T15:50:04+00:00
🔌 MT5: connected ✅
📋 Active plans loaded: 2
⚙️ Live trading: OFF (alert-only) 🔴
```

### Human-readable order rejections

MT5 retcodes are mapped to plain English (`RETCODE_MESSAGES` in
`app/mt5_handler.py`) and surfaced in the Telegram alert and the journal, e.g.
`10016 -> "Invalid stops - SL/TP too close to price or on the wrong side."` and
`10019 -> "Not enough money / free margin for this volume."`

### Watchdog script

`scripts/watchdog.py` polls `/health` every **3 minutes** and sends a Telegram
alert when it fails **twice in a row**:

```powershell
python scripts/watchdog.py                 # run forever (every 180s)
python scripts/watchdog.py --once          # single check (Task Scheduler / cron)
python scripts/watchdog.py --interval 30 --url http://127.0.0.1:5000/health
python scripts/watchdog.py --once --dry-run # log-only, sends no Telegram
python scripts/watchdog.py --ping-url https://hc-ping.com/<uuid>   # dead-man's switch
```

It reads `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` (and optional `WATCHDOG_URL`,
`WATCHDOG_INTERVAL`, `WATCHDOG_TIMEOUT`, `WATCHDOG_PING_URL`) from the
environment / `.env`. Register it with Task Scheduler (trigger *every 3
minutes*, action `scripts\watchdog.py --once`) so a crash cannot take the
watchdog down with it. Tune `--interval` shorter (e.g. 30s) during active
sessions and longer outside them.

**Dead-man's switch (do this).** The watchdog runs *on the VPS*. If the VPS
itself dies, so does the watchdog and you hear nothing. `--ping-url` solves
this: on every successful check the watchdog pings an external service such as
[healthchecks.io](https://healthchecks.io) (free). If the pings stop, *that*
service alerts you — the only way to notice a dead machine. See
[SETUP.md](#12-make-it-run-247) for the exact setup.

### Single order-send choke point

Every order the system can ever place goes through one private method,
`MT5Handler._send_order()`, which (a) refuses to send when live trading is
disabled, (b) runs `mt5.order_check()` first, and (c) logs the full request and
result. `execute_plan` and `close_position` both delegate to it, and a unit test
*statically scans the source* and fails if any `mt5.order_send` call appears
outside `_send_order` — so a future modify / partial-close / close-all path
cannot accidentally bypass the safety guard.

### Kill switch (`/panic` and `/resume`)

Disabling trading no longer requires editing `.env` and restarting. The runtime
override flips in memory:

```powershell
# Flip trading OFF immediately (returns the previous state)
curl -Method POST http://127.0.0.1:5000/panic -Headers @{ "X-API-Key" = "YOUR_API_KEY" }

# Flip it back ON
curl -Method POST http://127.0.0.1:5000/resume -Headers @{ "X-API-Key" = "YOUR_API_KEY" }
```

`/health/full` reports two fields:
- `live_trading_effective` — `true` **only** when both `.env` (`LIVE_TRADING`)
  *and* the runtime override allow trading.
- `panic` — whether the kill switch is currently engaged.

Order paths read `live_trading_effective` at the choke point, so a panic stops
new orders **and** closes immediately, with no restart. The Telegram command
`/panic` / `/resume` does the same thing from your phone.

### Telegram two-way control (`--telegram-listen`)

Start the server with the optional command listener and your phone becomes the
control surface:

```powershell
python main.py --telegram-listen
```

Only messages from your configured `TELEGRAM_CHAT_ID` are accepted. Commands:

| Command | Effect |
|---------|--------|
| `/status` | MT5 state, live-trading state, kill-switch state, open-position count |
| `/plans`  | List active plans |
| `/stale`  | List plans active > 30 days |
| `/panic`  | Engage the kill switch (runtime) |
| `/resume` | Clear the kill switch (runtime) |
| `/close <ticket>` | Close a position (blocked while trading is disabled) |
| `/help`   | Command list |

Every MT5-touching command goes through the handler, so it respects
`LIVE_TRADING` and the kill switch.

### Pre-flight risk check

Before anything is sent, `MT5Handler._preflight_check(plan)` rejects orders that
break a sanity bound — catching the fat-finger that costs real money (e.g.
`volume: 10` instead of `0.10`):

| Limit | Env var | Default |
|-------|---------|---------|
| Max volume (lots) | `MAX_VOLUME` | `1.0` |
| Min SL distance (points) | `MIN_SL_POINTS` | `50` |
| Max loss at SL (account currency) | `MAX_RISK_PER_TRADE` | `50.0` |

Rejections return the internal retcode `90001`, mapped in `RETCODE_MESSAGES` and
surfaced in the Telegram alert/journal like any broker rejection.

### Order-failure insight

`MT5Handler` keeps an in-memory histogram of every `order_send` retcode,
exposed as `retcode_histogram` and `top_non_done_retcodes` by `/stats/today`.
MT5 retcodes are translated to plain English (`RETCODE_MESSAGES`), so a 2 AM
alert says *"Invalid stops — SL/TP too close to price"* rather than `10016`.

### Daily digest

`scripts/daily_digest.py` sends one Telegram message a day (times UTC): plans
triggered today, active/stale plan counts, MT5 uptime %, unauthorized webhook
count, and the top non-DONE retcodes.

```powershell
python scripts/daily_digest.py            # fetch + send
python scripts/daily_digest.py --dry-run  # print only
```

Schedule it with Task Scheduler (daily, e.g. 21:00 UTC). It reads `API_KEY`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` and optional `DIGEST_URL` from `.env`.

### Run as a Windows Service (survives RDP disconnect & reboot)

`python main.py` in a terminal dies when RDP disconnects or the VPS reboots.
Install it as a Windows Service with NSSM (restart-on-failure, auto-start on
boot):

```powershell
# 1. Download NSSM from https://nssm.cc/download and put nssm.exe on PATH
#    (or drop it in the scripts\ folder).
# 2. Right-click scripts\install_service.bat -> Run as administrator
```

The script configures `AppExit Default Restart`, a 10-second restart delay, a
10-second restart throttle (crash-loop protection — NSSM has no native
max-restart count), a 15-second graceful stop, and rotates the service
stdout/stderr logs at 10 MB in `logs\`. Manage it with:

```powershell
sc query TradingAssistant
nssm restart TradingAssistant
nssm remove TradingAssistant confirm
```

### Static webhook URL (do not use a free random Ngrok URL)

Free Ngrok gives you a **new URL every restart**, which silently breaks your
TradingView alert. Use a **reserved** Ngrok domain or — recommended and free —
**Cloudflare Tunnel**, which gives you a permanent hostname. See
[SETUP.md §6a](SETUP.md#6a-static-alternative-cloudflare-tunnel) for the exact
commands. Whatever you choose, watch `/health/full`'s `last_webhook_received` to
confirm alerts are still arriving.

### `--dry-run` mode

Boot the server in alert-only mode without editing `.env`:

```powershell
python main.py --dry-run
```

This forces `LIVE_TRADING=false` (overriding `.env`), so signals are matched,
notified and journaled but **no orders are placed**. The safety guard is a
single choke point shared by *every* order path (`execute_plan`,
`close_position`, and any future modify call).

---

## Security notes

- **Never commit `.env`.** It is already in `.gitignore`. The repo ships only
  `.env.example` with placeholders.
- **Rotate `API_KEY` regularly.** Generate one with
  `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Update `.env`
  and the MT5 EA input (and any TradingView alert) after rotating.
- **Every webhook requires `X-API-Key`.** Requests without it get `401`; this is
  logged with the source IP.
- **Ngrok:** always use HTTPS and, ideally, protect the tunnel with
  `ngrok http 5000 --basic-auth="user:strongpass"` (or a reserved domain with
  access control). Treat the ngrok URL as a secret.
- **Firewall:** do not expose port 5000 directly to the internet. Bind the Flask
  server to `127.0.0.1` if only the local MT5 EA and Ngrok (running on the same
  host) need to reach it, or restrict inbound rules to the Ngrok/local traffic.
- **Least privilege:** use a **demo account** first, then a dedicated live
  account with limited funds. Prefer an MT5 *investor* (read-only) password is
  not usable for trading — use the master password only where required.
- **Notion:** the integration only needs access to the one database you share
  with it. Keep the secret out of version control.
- **Alert-only mode:** set `LIVE_TRADING=false` to test the entire pipeline
  (matching + notifications + journaling) with zero risk of placing an order.

---

## Design decisions and assumptions

Because the specification left some details open, the following reasonable
assumptions were made and are documented here:

1. **No auto-deactivation of plans.** After a plan fires, its status is *not*
   changed automatically, so you stay in control. Mark it `triggered`/`disabled`
   yourself (via JSON or `PlanMatcher.update_plan_status`) to stop it re-firing.
   This also satisfies the "tolerate duplicate signals" requirement: duplicates
   within a 10-second window are logged as warnings but still processed.
2. **Action inference.** TradingView signals may carry `action`/`side`
   (`buy`/`sell`, plus `long`/`short` aliases). MT5 signals carry `direction`
   (`above` → buy, `below` → sell). The matcher normalises both.
3. **Alert-only switch.** `LIVE_TRADING` was added (not in the original spec) so
   the whole pipeline can be exercised safely before going live.
4. **`MetaTrader5` pin.** The spec asked for `MetaTrader5==5.0.45`, which has no
   wheel for Python 3.12. `requirements.txt` uses `MetaTrader5>=5.0.45` so both
   Python 3.11 (5.0.45+) and 3.12 (5.0.47+) install a working build.
5. **Graceful MT5 import.** `app/mt5_handler.py` imports `MetaTrader5`
   defensively, so the server, matcher, notifier and their tests run on any OS.
   On a non-Windows box the handler reports `connected == False`.
6. **Notion schema-agnostic.** Rather than requiring specific columns, the
   notifier discovers the database's title property and writes the details into
   the page body. Any database works.
7. **Single user, single account.** There is one shared `API_KEY` and one set of
   broker credentials; no multi-tenancy.
8. **Broker symbol suffix.** Plans use the base symbol; `MT5_DEFAULT_SUFFIX` is
   applied only at execution time.
9. **JSON storage.** Plans live in `plans/plans.json`. If you outgrow it, SQLite
   is the recommended upgrade (the matcher's public methods would stay the same).
10. **MQL5 extras.** The EA adds `InpTouchPoints` (touch tolerance, default 10)
    and `InpTimeoutMs` (WebRequest timeout) beyond the required inputs to make
    the touch behaviour configurable.

---

## License

Provided as-is for personal use. Trading involves substantial risk; you are
responsible for every order this software places.
