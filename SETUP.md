# SETUP — From a Blank Windows VPS to a Running Trading Assistant

This is a hand-held, click-by-click walkthrough written for a **non-developer**.
Work through it in order. Wherever you see a block like:

> **📷 Screenshot placeholder:** *what you should be looking at*

that is a description of the screen so you can confirm you are in the right
place. Take a moment to check each one before moving on.

Total time: about **45–60 minutes** the first time.

> ⚠️ **Do all of this on a DEMO account first.** Nothing here is reversible if
> you let it trade real money before you understand it.

---

## Contents

1. [Provision the VPS](#1-provision-the-vps)
2. [Install Python and MT5](#2-install-python-and-mt5)
3. [Clone the project and install dependencies](#3-clone-the-project-and-install-dependencies)
4. [Configure `.env`](#4-configure-env)
5. [Start the server and test `/health`](#5-start-the-server-and-test-health)
6. [Set up Ngrok](#6-set-up-ngrok)
   - [Static alternative: Cloudflare Tunnel](#6a-static-alternative-cloudflare-tunnel)
7. [Set up the Telegram bot](#7-set-up-the-telegram-bot)
8. [Configure TradingView alerts](#8-configure-tradingview-alerts)
9. [Compile and attach the MQL5 EA](#9-compile-and-attach-the-mql5-ea)
10. [Set up Notion (or Obsidian sync)](#10-set-up-notion-or-obsidian-sync)
11. [Run a full end-to-end test](#11-run-a-full-end-to-end-test)
12. [Make it run 24/7](#12-make-it-run-247)
13. [Rotate your secrets](#13-rotate-your-secrets)

---

## 1. Provision the VPS

1. Create a **Windows Server 2019 or 2022** VPS (2 vCPU / 4 GB RAM is plenty) at
   any provider (Contabo, Vultr, AWS Lightsail, Hetzner, …). Choose the
   **closest region to your broker's server** to reduce latency.
2. Connect to it with **Remote Desktop (RDP)**. On your own PC: Start → *Remote
   Desktop Connection* → enter the VPS IP → log in with the credentials your
   provider emailed you.

   > **📷 Screenshot placeholder:** A Windows desktop with only a Recycle Bin
   > icon — that is a fresh VPS. You are in the right place.

3. Open **Server Manager** (usually auto-starts). You can ignore most of it.
4. Make sure your VPS has **outbound internet** (open a browser and load a page).

---

## 2. Install Python and MT5

### Python (64-bit)

1. Open a browser on the VPS and go to <https://www.python.org/downloads/windows/>.
2. Download **Python 3.12** — the **Windows installer (64-bit)**.
   > **📷 Screenshot placeholder:** On the download page, confirm the button says
   > *"Windows installer (64-bit)"*. Do **not** pick the 32-bit ("x86") one.
3. Run the installer. **Tick "Add python.exe to PATH"** at the bottom of the
   first screen. Click *Install Now*.
   > **📷 Screenshot placeholder:** The installer window with a checkbox at the
   > bottom that says *"Add python.exe to PATH"* — it must be ticked.
4. When it finishes, click *Close*.
5. Verify: open **Command Prompt** (Start → type `cmd` → Enter) and run:

   ```cmd
   python --version
   ```

   You should see `Python 3.12.x`. If you see an error, re-run the installer and
   make sure the PATH box was ticked.

### MetaTrader 5

1. Install the **MT5 terminal from your broker** (brokers usually give you a
   custom installer — use theirs so the correct servers are included).
2. Launch MT5 and **log into your demo account** once, manually. Note down the
   exact **server name** (e.g. `ICMarkets-Demo`) — you need it later.
   > **📷 Screenshot placeholder:** The MT5 login dialog showing *Login*,
   > *Password* and *Server* fields. The exact text in *Server* is what goes
   > into `.env`.
3. In MT5: **Tools → Options → Expert Advisors**:
   - Tick **Allow automated trading** (Algo Trading).
   - We will add the webhook URL to *Allow WebRequest for listed URL* in step 9.
4. Click **OK**.

> **Where is MT5 installed?** Right-click the MT5 shortcut → *Properties* → read
> the *Target*. It ends in `terminal64.exe`. The folder path (without the file
> name) is your `MT5_PATH`. Example:
> `C:\Program Files\MetaTrader 5\terminal64.exe`.

---

## 3. Clone the project and install dependencies

1. Install **Git** from <https://git-scm.com/download/win> (accept the defaults).
2. Open **Command Prompt** and run:

   ```cmd
   cd C:\
   git clone https://github.com/tex-node/chartasst.git
   cd chartasst
   ```

   > **📷 Screenshot placeholder:** `git clone` printing lines ending in
   > *"done."*, then a `C:\chartasst>` prompt.
   >
   > *If you were given the code as a ZIP instead, unzip it so that you have a
   > folder `C:\chartasst` containing `main.py`.*

3. Create an isolated Python environment and install the dependencies:

   ```cmd
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

   > **📷 Screenshot placeholder:** The prompt changes to start with `(.venv)`,
   > and `pip` prints a long list ending in *"Successfully installed ..."*.

   If `pip install` fails on `MetaTrader5`, you installed the **32-bit** Python.
   Uninstall it and redo step 2 with the 64-bit installer.

---

## 4. Configure `.env`

1. Copy the example file:

   ```cmd
   copy .env.example .env
   notepad .env
   ```

2. Fill in the values. At minimum:

   | Variable            | What to put there                                                    |
   |---------------------|----------------------------------------------------------------------|
   | `MT5_LOGIN`         | Your account number (digits only).                                   |
   | `MT5_PASSWORD`      | Your MT5 **master** password.                                        |
   | `MT5_SERVER`        | The exact server name you noted in step 2.                           |
   | `MT5_PATH`          | Full path to `terminal64.exe`.                                       |
   | `MT5_DEFAULT_SUFFIX`| Broker suffix like `.r` if your symbols have one; else leave blank.  |
   | `TELEGRAM_BOT_TOKEN`| From step 7 (you can come back and fill this in).                    |
   | `TELEGRAM_CHAT_ID`  | From step 7.                                                         |
   | `SMTP_HOST` etc.    | Optional. Fill in to also receive every alert by email (see README). |
   | `NOTIFY_EMAIL_TO`   | Email recipient (default `nodeswavemonitor@gmail.com`).              |
   | `API_KEY`           | A long random secret. Generate one (below).                          |
   | `LIVE_TRADING`      | Keep `false` for now — alert-only, no orders.                        |

3. Generate a strong `API_KEY`. In the Command Prompt (with `.venv` active) run:

   ```cmd
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

   Copy the output and paste it as the value of `API_KEY`.

4. Save the file (**Ctrl+S**) and close Notepad.

> **📷 Screenshot placeholder:** Your `.env` in Notepad with the MT5 block and
> the `API_KEY` line filled in. There should be **no spaces** around the `=`.

---

## 5. Start the server and test `/health`

1. In the Command Prompt (still in `C:\chartasst`, `.venv` active):

   ```cmd
   python main.py
   ```

   > **📷 Screenshot placeholder:** A wall of log lines starting with
   > `Trading Assistant starting up` and ending at a line like
   > `* Running on http://0.0.0.0:5000`. Leave this window **open** — it is your
   > server.

2. Open a **second** Command Prompt window and run:

   ```cmd
   curl http://127.0.0.1:5000/health
   ```

   You should see:

   ```json
   {"status":"healthy","mt5_connected":true}
   ```

   - **`healthy` / `mt5_connected:true`** → perfect, continue.
   - **`degraded` / `mt5_connected:false`** → the server is running but not
     logged in to MT5. Check the server window for a line beginning
     `mt5.initialize() failed` — it tells you exactly which credential is wrong.
     Fix `.env`, press **Ctrl+C** in the server window, and run `python main.py`
     again.

Leave the server running for the next steps. (Keep the `.env` window handy.)

---

## 6. Set up Ngrok

TradingView is on the internet and needs a public URL to reach your VPS. Ngrok
creates one safely.

1. Create a free account at <https://ngrok.com> and download the Windows ZIP.
2. Unzip it to `C:\ngrok`.
3. In the Ngrok dashboard under **Your Authtoken**, copy your token.
4. In a **new** Command Prompt:

   ```cmd
   cd C:\ngrok
   ngrok config add-authtoken YOUR_TOKEN
   ngrok http 5000 --basic-auth="trader:pickastrongpassword"
   ```

   > **📷 Screenshot placeholder:** A screen titled *ngrok* with a
   > **Forwarding** line reading `https://something.ngrok-free.app -> http://localhost:5000`.
   > Copy the `https://...ngrok-free.app` part.

5. **Write down that URL.** Your TradingView webhook will be
   `https://something.ngrok-free.app/webhook/tradingview`.

> The `--basic-auth` flag means only someone with the username/password can reach
> your endpoint. TradingView supports basic auth in the webhook URL, e.g.
> `https://trader:pickastrongpassword@something.ngrok-free.app/webhook/tradingview`.

Keep this Ngrok window open too.

> ⚠️ **Free Ngrok changes the URL every time it restarts.** When that happens your
> TradingView alert silently stops delivering. For anything you rely on daily,
> use a **reserved** Ngrok domain or, better, the **Cloudflare Tunnel** below —
> both give you a permanent address.

---

## 6a. Static alternative: Cloudflare Tunnel

Cloudflare Tunnel is free and gives you a **permanent hostname**, so your
TradingView webhook URL never changes. You need a domain on Cloudflare (you can
use the free plan). The steps below are for Windows.

1. **Install `cloudflared`** in a Command Prompt (or download the `.exe` from
   <https://github.com/cloudflare/cloudflared/releases> and put it on PATH):

   ```cmd
   winget install --id Cloudflare.cloudflared
   ```

2. **Log in** — this opens a browser; pick the domain you want to use:

   ```cmd
   cloudflared tunnel login
   ```

   > **📷 Screenshot placeholder:** A browser page asking you to *Authorize* a
   > domain. After clicking, a certificate file is saved under
   > `%USERPROFILE%\.cloudflared\`.

3. **Create the tunnel** (choose any name):

   ```cmd
   cloudflared tunnel create trading-assistant
   ```

   This prints a **Tunnel ID** (a UUID) and writes
   `%USERPROFILE%\.cloudflared\<TUNNEL_ID>.json`. Write the ID down.

4. **Point a hostname at it** (replace `ta.yourdomain.com` with a subdomain you
   control):

   ```cmd
   cloudflared tunnel route dns trading-assistant ta.yourdomain.com
   ```

   > **📷 Screenshot placeholder:** A confirmation line like *"Added CNAME
   > ta.yourdomain.com which will route to this tunnel"*.

5. **Create the config file** at `%USERPROFILE%\.cloudflared\config.yml`
   (Notepad). Replace the ID and hostname with yours:

   ```yaml
   tunnel: YOUR_TUNNEL_ID
   credentials-file: C:\Users\YourUser\.cloudflared\YOUR_TUNNEL_ID.json
   ingress:
     - hostname: ta.yourdomain.com
       service: http://localhost:5000
     - service: http_status:404
   ```

6. **Run the tunnel** (keep this window open, like Ngrok):

   ```cmd
   cloudflared tunnel run trading-assistant
   ```

   > **📷 Screenshot placeholder:** Log lines showing
   > *"Registered tunnel connection"* and your hostname.

7. **Your webhook URL is now permanent:**

   ```text
   https://ta.yourdomain.com/webhook/tradingview
   ```

   Put that into the TradingView alert (step 8) and you never have to update it
   again — even after a reboot.

8. **Run it as a Windows service** so it survives reboots:

   ```cmd
   cloudflared service install
   ```

   This installs a *Cloudflare Tunnel* service that uses your `config.yml` and
   starts automatically.

> **Security:** by default the hostname is public. To lock it down for free,
> open **Cloudflare Zero Trust → Access → Applications**, add a *Self-hosted*
> app for `ta.yourdomain.com`, and create a policy that only allows you (e.g.
> by email). You can also keep the Ngrok `--basic-auth` approach if you prefer.

---

## 7. Set up the Telegram bot

1. In the Telegram app, search for **@BotFather** and start a chat.
2. Send `/newbot`, choose a name and a username (must end in `bot`).
3. BotFather replies with a token like
   `123456789:AAExampleTokenStringFromBotFather`. **Copy it** → this is
   `TELEGRAM_BOT_TOKEN`.

   > **📷 Screenshot placeholder:** BotFather's message containing
   > *"Use this token to access the HTTP API:"* followed by the token.

4. **Send your new bot a message.** Open the bot's chat (BotFather gives you a
   link) and send `/start` or just `hi`. This is required, otherwise the bot
   cannot message you first.
5. Find your **chat ID**. The easiest way:

   ```cmd
   curl https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```

   Look for `"chat":{"id":123456789,...}`. That number (or negative for groups)
   is `TELEGRAM_CHAT_ID`.

   Alternatively, message **@userinfobot** and it replies with your ID.

6. Put both values into `.env`, save, then restart the server (Ctrl+C in the
   server window, then `python main.py`).

7. **Test it now** — from a Command Prompt:

   ```cmd
   curl -Method POST "https://api.telegram.org/bot<YOUR_TOKEN>/sendMessage" -ContentType "application/json" -Body "{\"chat_id\":\"<YOUR_CHAT_ID>\",\"text\":\"Trading Assistant online\"}"
   ```

   Your phone should buzz. If not, recheck the token and chat ID.

---

## 8. Configure TradingView alerts

1. On TradingView, open the chart you trade and create an alert (**Alt + A**).
2. **Condition:** pick your script/level condition.
3. Under **Notifications**, tick **Webhook URL** and paste:

   ```text
   https://trader:pickastrongpassword@something.ngrok-free.app/webhook/tradingview
   ```

   (That is the basic-auth URL from step 6.)

4. In the **Message** box paste JSON that includes `symbol` and `action`:

   ```json
   {"symbol":"EURUSD","action":"buy","price":{{close}},"timeframe":"H1","condition":"Break above 1.0850"}
   ```

5. **Webhook headers** — add your API key (if your TradingView plan supports
   custom headers):

   ```text
   X-API-Key: <the API_KEY from your .env>
   ```

   > **📷 Screenshot placeholder:** The TradingView alert dialog showing
   > *Webhook URL* ticked, the URL box filled, and (if available) a headers
   > field with `X-API-Key`.
   >
   > If your plan cannot set headers, either rely on the Ngrok basic-auth above
   > or select a plan that allows headers. Never email or share your API key.

6. Save the alert.

---

## 9. Compile and attach the MQL5 EA

1. In MT5, press **F4** to open **MetaEditor**.
2. In MetaEditor: **File → Open**, browse to `C:\chartasst\mql5\ChartObjectMonitor.mq5`.
3. Press **F7** to compile.
   > **📷 Screenshot placeholder:** The *Errors* tab at the bottom showing
   > `0 errors, 0 warnings`.
4. Back in MT5: **Tools → Options → Expert Advisors**, tick **Allow WebRequest
   for listed URL** and add:

   ```text
   http://127.0.0.1:5000
   ```

   Click **OK**.
   > **📷 Screenshot placeholder:** The options dialog with a checkbox
   > *"Allow WebRequest for listed URL"* and a list box containing the URL.

5. Open the chart you want monitored (e.g. EURUSD H1). From the **Navigator**
   panel, expand **Expert Advisors**, drag **ChartObjectMonitor** onto the chart.
6. In the settings dialog:
   - `InpWebhookURL` = `http://127.0.0.1:5000/webhook/mt5`
   - `InpAPIKey` = your `API_KEY`
   - `InpMonitorColor` = `Red` (or whatever colour you draw levels in)
   - `InpAlertCross` = true, `InpAlertTouch` = true, `InpCooldownSec` = 30
   - Click **OK**.
7. Make sure the **Algo Trading** button in the MT5 toolbar is **green**.
   > **📷 Screenshot placeholder:** The MT5 toolbar with the *Algo Trading*
   > button highlighted green, and a smiley face icon in the top-right of the
   > chart.
8. In the **Experts** tab at the bottom of MT5 you should see:
   `ChartObjectMonitor initialised.` and your webhook URL.
9. Right-click the chart → **Object List**. Rename a red horizontal line to
   match a plan's `object_name`, e.g. `Resistance_1.0850`.
   > **📷 Screenshot placeholder:** The Object List window with a line named
   > *Resistance_1.0850*.

---

## 10. Set up Notion (or Obsidian sync)

**Skip this step if you only want Telegram alerts.**

### Option A — Notion

1. Go to <https://www.notion.so/my-integrations> → **New integration**, name it,
   copy the **Internal Integration Secret** → `NOTION_API_KEY`.
2. Open (or create) a database for trades. Any schema is fine.
3. On the database page: **••• → Connections → Connect to → your integration**.
4. Copy the **database ID** from the page URL
   (`https://www.notion.so/<workspace>/<DATABASE_ID>?v=...`) → `NOTION_DATABASE_ID`.
5. Put both values in `.env` and restart the server.

### Option B — Obsidian

1. Set `OBSIDIAN_VAULT_PATH` in `.env` to your vault folder, e.g.
   `C:\Users\You\Documents\MyVault`.
2. To read trades on your phone, keep the vault inside a synced folder (OneDrive,
   Google Drive, Dropbox, iCloud) or use **Obsidian Sync**, then open the same
   vault in the Obsidian mobile app.
3. Trades are appended to `Trade_Logs\YYYY-MM-DD.md` automatically.

---

## 11. Run a full end-to-end test

Keep `LIVE_TRADING=false` for the first pass — the assistant will match, notify
and journal, but place **no** order.

1. Make sure a plan is active in `plans/plans.json` for EURUSD buy (there is a
   `plan_001` seed you can use), and the server is running.
2. **Simulate a TradingView alert** from a Command Prompt:

   ```cmd
   curl -Method POST http://127.0.0.1:5000/webhook/tradingview ^
     -Headers @{ "X-API-Key" = "YOUR_API_KEY" } ^
     -ContentType "application/json" ^
     -Body "{\"symbol\":\"EURUSD\",\"action\":\"buy\",\"price\":1.0851}"
   ```

   Expected response:
   `{"status":"received","matched":true,"executed":false,...}`
   (`executed:false` because `LIVE_TRADING=false`).

3. **Check your phone** — Telegram should show a 🟢 BUY alert. ✅
4. **Check Notion / Obsidian** — a new entry should appear within a few seconds. ✅
5. **Test the unplanned path** — send a signal for a symbol with no active plan:

   ```cmd
   curl -Method POST http://127.0.0.1:5000/webhook/tradingview ^
     -Headers @{ "X-API-Key" = "YOUR_API_KEY" } ^
     -ContentType "application/json" ^
     -Body "{\"symbol\":\"USDJPY\",\"action\":\"buy\"}"
   ```

   You should get `"matched":false` and a ⚠️ *UNPLANNED SIGNAL* Telegram alert,
   and **no order**. ✅

6. **Test the MT5 path** — draw a red horizontal line named `Resistance_1.0850`
   near current EURUSD price so it gets crossed, or simply simulate the EA:

   ```cmd
   curl -Method POST http://127.0.0.1:5000/webhook/mt5 ^
     -Headers @{ "X-API-Key" = "YOUR_API_KEY" } ^
     -ContentType "application/json" ^
     -Body "{\"event\":\"cross\",\"object_name\":\"Resistance_1.0850\",\"price\":1.0850,\"direction\":\"above\",\"symbol\":\"EURUSD\",\"timeframe\":\"PERIOD_H1\"}"
   ```

   You should get `"matched":true` and a Telegram alert. ✅

7. If everything works, **go live**: set `LIVE_TRADING=true` in `.env`, restart
   the server, and repeat step 2. This time, on a **demo** account, an order will
   be placed and `executed` will be `true`. Verify it on the MT5 **Trade** tab.

   > **📷 Screenshot placeholder:** MT5 *Toolbox → Trade* tab showing a new
   > position with your volume and the comment `TA-plan_001`.

8. Only after you are fully confident on demo should you point the system at a
   live account.

---

## 12. Make it run 24/7

So the server restarts automatically after a reboot or crash:

1. Open **Task Scheduler** → **Create Task**.
2. **General:** name it `TradingAssistant`; select **Run whether user is logged
   on or not**.
3. **Triggers → New:** *At startup*.
4. **Actions → New:**
   - Program/script: `C:\chartasst\.venv\Scripts\python.exe`
   - Add arguments: `main.py`
   - Start in: `C:\chartasst`
5. **Settings:** tick *If the task fails, restart every 1 minute* (max 3 times),
   and *Stop the task if it runs longer than* → untick.
6. Click **OK** and enter your Windows password when prompted.

Ngrok must also stay open. Run it the same way (a startup task with program
`C:\ngrok\ngrok.exe` and arguments
`http 5000 --basic-auth="trader:pickastrongpassword"`), or use a reserved domain
so the URL never changes.

> On first reboot, re-check that `/health` returns `healthy`. If not, review the
> Server's own logs by adding a `logging.FileHandler` in `main.py`, or simply run
> `python main.py` manually to read the error.

### Easier alternative: the installer script

Instead of configuring Task Scheduler by hand, run the included NSSM installer
(right-click → **Run as administrator**):

```cmd
scripts\install_service.bat
```

It registers the service with **restart-on-failure** and logs to `logs\`.

### Keep an eye on it

- **Logs:** `logs\trading-assistant.log` (rotates at 10 MB, keeps 5 backups) and
  `logs\service-out.log`.
- **Health:** open `http://127.0.0.1:5000/health/full` to see MT5 state, open
  positions and the `last_webhook_received` time.
- **Watchdog:** schedule `python scripts\watchdog.py --once` to run **every 3
  minutes** in Task Scheduler. It messages your Telegram if `/health` fails
  twice in a row, so you hear about a problem before it costs you a trade.
- **Daily digest:** schedule `python scripts\daily_digest.py` to run **once a
  day** (e.g. 21:00 UTC). One Telegram message with triggers, plan counts,
  uptime and top order failures.
- **Phone control (optional):** add `--telegram-listen` to the service's
  arguments to enable `/status`, `/plans`, `/stale`, `/panic`, `/resume`,
  `/close <ticket>` and `/help`. Off by default.
- **Panic from your phone:** `/panic` disables trading instantly (in memory);
  `/resume` re-enables it. No restart, no SSH.

### Dead-man's switch: know when the whole VPS dies

The watchdog runs *on the VPS*. If the VPS itself goes offline, so does the
watchdog, and you get **no** alert. Fix this with a free external service that
expects a regular "I'm alive" ping and alerts you when the pings stop.

**Using [healthchecks.io](https://healthchecks.io) (free):**

1. Sign up and click **+ Add Check**.
2. Give it a name (e.g. `Trading Assistant VPS`).
3. Set **Period** to `5 minutes` and **Grace Time** to `5 minutes` (so it only
   complains after ~10 minutes of silence).
4. Copy the **Ping URL** — it looks like
   `https://hc-ping.com/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`.
5. Add it to your `.env`:

   ```text
   WATCHDOG_PING_URL=https://hc-ping.com/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
   ```

6. Set up the alert channel in healthchecks.io (**Integrations → Email /
   Telegram / Slack**) so it notifies you the same way your Telegram bot does.

Now every successful local health check also pings healthchecks.io. Your
watchdog Task Scheduler entry should be:

```cmd
C:\chartasst\.venv\Scripts\python.exe C:\chartasst\scripts\watchdog.py --once
```

> **📷 Screenshot placeholder:** healthchecks.io dashboard showing your check
> as a green "up" dot with a *Last Ping* a few minutes old. If it ever turns red
> and pings you, the VPS or the tunnel is down.

**How this layers:** the *internal* watchdog tells you when the server is
unhealthy; the *external* dead-man's switch tells you when the machine itself is
gone. Together they cover both failure modes.

> **UptimeRobot** works too: create an **HTTP(S)** monitor for your public
> tunnel URL ending in `/health/full` (not `127.0.0.1`, which UptimeRobot cannot
> reach), keyword `"status":"healthy"`, checked every 5 minutes.

---

## 13. Rotate your secrets

You have **four secrets** in `.env`. Keep a copy of this list (and where each
comes from) in your own password manager — **never in the repo**.

| Secret | Env var | Where it comes from | What a leak lets an attacker do |
|--------|---------|---------------------|----------------------------------|
| MT5 account password | `MT5_PASSWORD` | Your broker | Trade and withdraw on your account |
| API key | `API_KEY` | You generate it | Send fake webhooks / call `/panic`, `/close`, `/stats` |
| Telegram bot token | `TELEGRAM_BOT_TOKEN` | @BotFather | Impersonate the bot, read its updates |
| Email (SMTP) password | `SMTP_PASSWORD` | Your email provider (Gmail: an App Password) | Send email as you, read your mailbox |
| Notion integration secret | `NOTION_API_KEY` | notion.so/my-integrations | Read/write the connected database |

### Before rotating anything

1. Confirm which secret is involved. Check `unauthorized_count` in
   `/health/full` or `/stats/today` — a spike means someone is hitting your
   webhook URL (the API key is the likely leak).
2. Tell no one, and do not paste the old value into logs or chats.

### How to rotate each one

**API key (most important):**
1. Generate a new one:
   ```cmd
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
2. Put it in `.env` as `API_KEY`, and update the MT5 EA's `InpAPIKey` and any
   TradingView webhook headers.
3. Restart the service (`nssm restart TradingAssistant`).
4. **Why urgent:** if your tunnel URL was scanned, the old key may already be
   known — rotate immediately when `unauthorized_count` jumps.

**Telegram bot token:**
1. In @BotFather send `/revoke` and pick the bot (or `/token` to regenerate).
2. Paste the new token into `TELEGRAM_BOT_TOKEN`, restart the service.

**MT5 password:**
1. Change it in your broker's client portal (this logs the terminal out).
2. Update `MT5_PASSWORD`, restart the service, and confirm `/health` returns
   `healthy`.

**Email (SMTP) password / app password:**
1. In your email provider's security settings, revoke the old app password and
   create a new one (Gmail: Security → App passwords).
2. Update `SMTP_PASSWORD` in `.env`, restart the service.
3. Confirm a notification email arrives (trigger a test signal).

**Notion integration secret:**
1. In the integration settings, click **Regenerate** (or delete + create a new
   integration and re-share the database).
2. Update `NOTION_API_KEY`, restart, and trigger a test webhook to confirm the
   trade still lands in Notion.

### Verify the rotation succeeded

```cmd
curl http://127.0.0.1:5000/health/full
```

- `mt5_connected` = `true` → MT5 credentials good.
- Send a test TradingView webhook with the **new** API key → `matched`/`executed`
  response means the key works.
- Send it with the **old** key → expect `401` (proves the old key is dead).
- Send a matched signal and check Telegram + Notion/Obsidian both received it.

> **Tip:** rotate `API_KEY` on a schedule (e.g. every 90 days) and immediately
> after any suspected exposure. With `CLOUDFLARE TUNNEL` + Cloudflare Access in
> front, this is a belt-and-braces setup.

---

## You are done

You now have a 24/7 assistant that watches your levels, fires only on your
pre-written plans, executes what you planned, and keeps a mobile-friendly trade
journal. Review `README.md` for the API reference, troubleshooting and security
notes, and run `pytest` after any change you make.
