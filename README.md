# Risk Monitor

Local FastAPI app that aggregates positions from Schwab, Coinbase, Strike, and Fidelity into a single portfolio view, with a bitcoin-focused view and a bitcoin-backed loans tracker. Runs on Windows in a Python venv.

## What it shows

Three views, one server, one master password.

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
- Aggregate row with weighted LTV = (Σ principal + Σ interest) ÷ (Σ collateral × current BTC price)
- Net Value = Net BTC × current BTC price → your dollar equity if every loan settled today

## Data sources

### Schwab (OAuth)
1. Register a developer app at [developer.schwab.com](https://developer.schwab.com)
2. Set the app's **Callback URL** to exactly `https://127.0.0.1:8182` (no trailing slash — Schwab requires an exact string match; the wrong slash is a common cause of "returned to login screen" symptoms)
3. Wait for Schwab approval, then copy the `client_id` and `client_secret` into the Risk Monitor setup form
4. On `/connect`, click **Authorize with Schwab** — a browser window opens Schwab login, you complete 2FA and approve access, Schwab redirects to `https://127.0.0.1:8182?code=...`
5. First browser hit to the callback URL shows a self-signed certificate warning — see **Trust the local cert** below to eliminate it going forward
6. Access tokens auto-refresh every ~30 min; the refresh token itself expires every ~7 days → hit **Reconnect** in the nav to redo the browser flow

### Coinbase (CDP API key)
1. Create a key at [portal.cdp.coinbase.com/access/api](https://portal.cdp.coinbase.com/access/api)
2. Grant **View** permission only (no trade permission needed for a read-only portfolio viewer)
3. Coinbase gives you a JSON file containing `name` and `privateKey`
4. Paste the entire JSON blob at `/coinbase` → **Save Coinbase key**
5. The app handles both older PEM-wrapped EC keys (ES256) and newer raw base64-encoded Ed25519 keys (EdDSA), auto-detecting which JWT signing algorithm to use

### Strike (API key)
1. Create a key at [dashboard.strike.me/settings/api](https://dashboard.strike.me/settings/api)
2. Grant read permissions (balances + rates)
3. Paste at `/strike` → **Save Strike key**
4. Balances sync via `/v1/balances`; reference BTC/USD price via `/v1/rates/ticker`
5. Loans are **not** exposed by the API — enter them by hand on `/loans`

### Fidelity (CSV import)
Fidelity has no public API — the app reads a downloaded positions CSV.

1. Log in at fidelity.com → **Accounts & Trade** → **Portfolio** → **Positions** → **Download** (upper right)
2. Choose "Positions from selected accounts" if you want a subset; otherwise all accounts export
3. Save the CSV into `data/` under this project (any filename starting with `Fidelity_` — e.g., `Fidelity_Jul-11-2026.csv`)
4. Click **Refresh** on `/main` — the app picks up the newest matching file, wipes prior Fidelity data, and re-imports
5. Quirks handled automatically:
   - Option symbols with leading ` -` prefix (Fidelity export convention) are normalized (`-IBIT270115C115` → `IBIT270115C115`)
   - `BROKERAGELINK` aggregate row in a 401K account is skipped so it doesn't double-count the linked BrokerageLink sub-account
   - Money-market rows (SPAXX**, FDRXX**, VMRXX) become `quantity = dollar amount, last price = $1`

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
