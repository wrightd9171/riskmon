# Risk Monitor

Local FastAPI app that aggregates positions from Schwab, Coinbase, Strike, Robinhood, and Fidelity (plus on-chain BTC addresses) into a single portfolio view, with a bitcoin-focused view, a bitcoin-backed loans tracker, and an optional weekly Pushover digest. Runs on Windows in a Python venv.

## What it shows

Three views + a weekly Pushover digest, one server, one master password.

### `/main` — Positions
- Every position from every connected account, aggregated per symbol, with per-account drill-down beneath each aggregate row
- Sortable columns: Symbol, Description, Last Price, Quantity, Market Value, Cost Basis Price, Cost Basis Value, Unrealized P&L
- Filters: by account (checkboxes), hide zero-value positions, hide positions with no cost-basis P&L
- Unrealized P&L color-coded (green positive, red negative)
- Total row at top summing Market Value, Cost Basis Value, and Unrealized P&L
- Prices ≥ $1,000 render with 0 decimals, smaller prices with 2; BTC quantity to 2 decimals, other quantities rounded to whole units
- BTC's aggregate row includes a synthetic **Strike Loan Equity** per-account entry = (collateral BTC) − (loan debt ÷ BTC price)

### `/crypto` — Virtual BTC exposure
- Grand total virtual BTC across all sources, plus per-category subtotals
- Categories: **Direct BTC** (Coinbase, Strike native), **BTC ETF (IBIT)** across every Fidelity account, **Long IBIT Options** (positive quantity only), **Strike Loan Equity**
- Toggle **Include all other positions** — adds an "Other Positions" category translating every non-BTC-related position (stocks, ETFs, cash) into virtual BTC via `market_value ÷ BTC price`

### `/loans` — Bitcoin-backed loans
- Manual entry (Strike API does not expose loans)
- Per-loan columns: origination, termination, principal, interest, Debt (BTC), Collateral (BTC), Net (BTC), Collateral Value, Net Value, LTV, notes
- Total row (at top) with weighted LTV = (Σ principal + Σ interest) ÷ (Σ collateral × current BTC price)
- Net Value = Net BTC × current BTC price → your dollar equity if every loan settled today

### `/settings` → Notify — Portfolio digest (Pushover)
- Optional push notification summarizing the portfolio, delivered to your phone via [Pushover](https://pushover.net)
- Digest contents: total market value, unrealized P&L, virtual BTC exposure, bitcoin-loan LTV, and the top 5 positions by market value (trimmed to Pushover's 1024-char limit)
- **Send test push now** button to verify the keys before scheduling
- The recurring send is run by an external Windows Task Scheduler job (`send_digest.py`), so it fires even when the app is closed — see the digest setup section

## Data sources

### Schwab (OAuth)

**Get a developer account and register an app** (~3-7 business days for approval):

1. Sign up at [developer.schwab.com](https://developer.schwab.com) with the same email tied to your Schwab brokerage account (they cross-check)
2. Log in, then go to **Dashboard** → **My Apps** → **Add a New App**
3. Fill out the app registration form:
   - **App Name**: any label (e.g., "Personal Risk Monitor")
   - **API Product**: select **Accounts and Trading Production**
   - **Callback URL**: enter exactly `https://127.0.0.1:8182` — **no trailing slash**. Schwab requires an exact byte-for-byte match; the wrong slash is the #1 cause of "you got redirected back to the Schwab login page after 2FA" symptoms
   - Description: whatever
4. Submit and wait. Schwab reviews manually. You'll get an email when the app moves from **Pending** to **Ready For Use**
5. Once approved, open the app in the dashboard and copy **App Key** (this is your `client_id`) and **App Secret** (this is your `client_secret`)

**Wire it into Risk Monitor:**

6. On the Risk Monitor setup page, paste the `client_id` and `client_secret`
7. On `/connect`, click **Authorize with Schwab** — a browser opens the Schwab login. Enter your Schwab creds + 2FA + approve. Schwab redirects to `https://127.0.0.1:8182?code=...`
8. First time only: your browser shows a self-signed cert warning on the callback URL — see **Trust the local cert** below to eliminate it going forward
9. Access tokens auto-refresh every ~30 min silently. The refresh token itself expires every ~7 days — when it does, click **Reconnect** in the nav to redo the browser OAuth flow

### Coinbase (CDP API key)

**Create a Trading API key on the Coinbase Developer Platform:**

1. Sign in to [portal.cdp.coinbase.com](https://portal.cdp.coinbase.com) with your Coinbase account credentials
2. In the left nav, go to **Access** → **API keys** (direct link: [portal.cdp.coinbase.com/access/api](https://portal.cdp.coinbase.com/access/api))
3. Click **Create API key**
4. Configure the key:
   - **Name**: any label (e.g., "Risk Monitor")
   - **Portfolio**: select **Default** (or a specific portfolio if you use them)
   - **Permissions**: check **View** only. Do NOT grant Trade or Transfer — this app only reads balances
   - **IP allowlist**: leave blank (unless you have a static IP; local dev doesn't need it)
   - **Signature algorithm**: leave default (Ed25519 for newer keys)
5. Click **Create & download**. A JSON file downloads containing `name` and `privateKey`. **Coinbase shows the private key only once** — save the file
6. On the Risk Monitor `/coinbase` page, paste the **entire JSON** into the "Credentials JSON" textarea and click **Save Coinbase key**

The app auto-detects both older PEM-wrapped EC keys (signed as ES256) and newer raw base64-encoded Ed25519 keys (EdDSA), so either format from Coinbase works.

### Strike (API key)

**Create a read-only API key:**

1. Sign in at [strike.me](https://strike.me/) and go to **Dashboard** → **Settings** → **API** (direct link: [dashboard.strike.me/settings/api](https://dashboard.strike.me/settings/api))
2. Click **Create API key** (or **Generate new key**)
3. Configure:
   - **Name**: any label
   - **Scopes/Permissions**: enable at least `balances.read` (and `rates.read` if listed separately). Do NOT grant payment/transfer scopes
4. Click **Create**. Strike shows the key **once** — copy it immediately
5. On the Risk Monitor `/strike` page, paste the key and click **Save Strike key**

Loans are **not** exposed by the Strike API — enter them by hand on `/loans`.

### Fidelity (CSV import)

Fidelity has no public API — the app reads a downloaded positions CSV.

1. Sign in at [fidelity.com](https://fidelity.com)
2. Go to **Accounts & Trade** → **Portfolio**
3. Click the **Positions** tab
4. In the upper right of the positions table, click the **Download** icon (⬇). Choose **CSV**
5. Save the file into the project's `data/` folder — filename must start with `Fidelity_` (e.g., `Fidelity_Jul-11-2026.csv`). This is where the CSV importer looks
6. Click **Refresh** on `/main` — the app picks up the newest matching file, wipes prior Fidelity data, and re-imports

Repeat whenever you want fresh Fidelity data (weekly, monthly — whatever cadence). The CSV always includes all four sub-accounts (Traditional IRA, 401K, BrokerageLink, HSA) if you're logged into a household view.

Quirks handled automatically:
- Option symbols with leading ` -` prefix (Fidelity export convention) are normalized (`-IBIT270115C115` → `IBIT270115C115`)
- `BROKERAGELINK` aggregate row in a 401K account is skipped so it doesn't double-count the linked BrokerageLink sub-account (which has its own line items)
- Money-market rows (SPAXX\*\*, FDRXX\*\*, VMRXX) become `quantity = dollar amount, last price = $1`

### On-chain / self-custody (BTC)

**No API key required** — balances come from public block-explorer data at [mempool.space](https://mempool.space).

1. Go to the `/onchain` page in the app
2. Under **Add a new address**, enter:
   - **Chain**: `BTC` (only BTC is supported today; ETH and others are structured to plug in later)
   - **Public address**: your receive address in any format — bech32 (`bc1...`), P2SH (`3...`), or legacy (`1...`)
   - **Label** (optional): a description like "Cold storage" or "Hardware wallet"
3. Click **Save address**
4. Repeat for as many addresses as you want — a single hardware wallet can have many (one per receive)
5. Click **Refresh** on `/main` — the app sums balances across all addresses per chain and inserts a single **On-chain BTC** position

Private keys are never touched. This is watch-only. Balances are confirmed on-chain only (mempool sits are excluded); pricing comes from mempool.space's public price feed.

If you use an xpub/zpub to derive many addresses, add each derived address individually or ask for xpub-derivation support (BIP32/BIP84) as a follow-up.

### Portfolio digest (Pushover + Windows Task Scheduler)

The digest is optional — a low-effort "how is everything doing" push to your phone. Scheduling is handled outside the app so it fires whether or not the app is running.

**Get Pushover keys:**

1. Create an account at [pushover.net](https://pushover.net) and install the Pushover app on your phone (one-time ~$5 per platform after a trial).
2. Your **user key** is on the dashboard home page.
3. Under **Your Applications → Create an Application/API Token**, make an app (e.g. "Risk Monitor") to get an **API token**.

**Configure** on `/settings` → **Notify**:
- Paste the **API token** and **user key** (both encrypted with your master password), tick **Enable the digest**, and **Save**.
- Click **Send test push now** to confirm a push lands on your phone.

**Schedule the recurring send** (runs even when the app is closed) — from the project root:

```
powershell -ExecutionPolicy Bypass -File scripts\register-notify-task.ps1
```

It prompts for the day/time and your master password, then registers a weekly Windows Task Scheduler job that runs `send_digest.py` on that cadence.

**Security tradeoff:** because the task decrypts your Pushover keys unattended, it must hold your master password. The script stores it in `%LOCALAPPDATA%\riskmon\notify-password.txt` — outside OneDrive and the repo, locked to your user account — and points the task at it via `RISKMON_MASTER_PASSWORD_FILE`. This is inherent to sending on a schedule without the app open and unlocked; if you'd rather not store the password, skip the task and use the **Send test push now** button manually.

Manage the task:
- Test now: `Start-ScheduledTask -TaskName 'RiskMonitor Portfolio Digest'`
- Remove: `Unregister-ScheduledTask -TaskName 'RiskMonitor Portfolio Digest' -Confirm:$false`
- Log: `%LOCALAPPDATA%\riskmon\notify.log`

## Setup

### Prerequisites
- Windows 10 or 11
- Python 3.11+ from [python.org](https://www.python.org/downloads/) with "Add Python to PATH" checked during install

### Install & run
```
cd C:\Users\wrigh\OneDrive\claude\port\risk-monitor
start.bat
```

First run creates a `.venv/`, installs dependencies from `requirements.txt`, then launches the server on `http://127.0.0.1:8000/`. Every subsequent run just launches. Ctrl+C stops it.

### Optional: nicer hostname
Append `127.0.0.1 riskmon` to `C:\Windows\System32\drivers\etc\hosts` (admin required). Then use `http://riskmon:8000/` instead of `http://127.0.0.1:8000/`.

### Trust the local cert (skip Schwab cert warnings)
The OAuth callback listener uses a self-signed cert. Chrome/Edge will warn about it once. To silence the warning permanently:
```
certutil -user -addstore Root "C:\Users\wrigh\OneDrive\claude\port\risk-monitor\data\cert.pem"
```
Then close and reopen the browser so it re-reads the trust store. To undo later: `certutil -user -delstore Root "127.0.0.1"`.

### First-run app flow
1. Open `http://riskmon:8000/` (or `http://127.0.0.1:8000/`)
2. **Setup page** — set a master password (min 8 chars) and paste your Schwab `client_id` + `client_secret`. The password is never stored; it derives an Argon2id key that encrypts a JSON blob at `data/secrets.enc`
3. **Connect Schwab** — browser OAuth as described above
4. Click **Refresh** on `/main` — Schwab positions sync
5. Head to `/coinbase`, `/strike`, and drop the Fidelity CSV into `data/`
6. Hit **Refresh** again — all four brokers sync in one shot

Subsequent starts: **Unlock** with master password → **Refresh** → view.

## Data & security

- All broker credentials live encrypted in `data/secrets.enc` (Argon2id KDF → Fernet AEAD). The master password is never stored on disk or in memory beyond the request that unlocks the store
- SQLite portfolio cache at `data/portfolio.db` — not encrypted. Holds current positions, account labels (last-4 digits only for Schwab), and bitcoin-loan records. Deleting the file forces a fresh sync
- Self-signed cert at `data/cert.pem` + `data/key.pem`, used only for the local OAuth callback listener on port 8182
- `data/` is `.gitignore`d — nothing sensitive ever gets committed or pushed
- OneDrive syncing `data/` across machines is safe because the credential blob is password-encrypted. The DB is only useful to someone who can also read your Windows profile

## Common issues

**"Your connection isn't private" during Schwab OAuth**
Trust the local cert once — see the certutil command above — then close/reopen the browser.

**Schwab redirects back to login after 2FA + approval**
The `redirect_uri` in your registered developer app must exactly match `https://127.0.0.1:8182` (no trailing slash). Any mismatch and Schwab silently drops you at the login screen.

**Refresh returns Internal Server Error after adding a broker**
Read the response body (browser dev tools → Network) — the app puts specific error detail in the 502 response. Usually credential format:
- Coinbase Ed25519 keys should be pasted as the JSON blob Coinbase gives you, not the private key alone
- Strike API keys should have read permissions on balances

**Refresh redirects to `/unlock`**
The server was restarted (or you clicked **Lock**). Enter your master password to decrypt secrets back into memory.

**Refresh token expired at Schwab (after ~7 days)**
Click **Reconnect** in the nav on `/main` — you'll do the browser OAuth flow again, which is quick with the cert already trusted.

## Architecture

- **FastAPI** + **Jinja2** templates + **SQLAlchemy** (SQLite)
- Broker modules under `app/{schwab,coinbase,strike,fidelity}/`, each exposing a `sync_all()` that hits the source and upserts positions
- `app/secrets_store.py` holds the encrypted-secrets singleton; `app/security.py` wraps Argon2 + Fernet
- `app/web/routes.py` glues it together — routes, filters, sort helpers, aggregation logic
- `app/schwab/oauth.py` runs a threaded HTTPS server on `127.0.0.1:8182` during authorization, then shuts itself down
