"""Fidelity positions via the `fidelity-api` Playwright scraper (best-effort).

This is inherently fragile: it drives a real browser, needs credentials, and
may hit 2FA. Any failure raises FidelityApiUnavailable so the caller can fall
back to the CSV import. Nothing here imports fidelity-api at module load, so the
app runs fine whether or not the library (and its Playwright browsers) is
installed.

Setup for the user (one time):
    pip install fidelity-api
    playwright install
Then enter the Fidelity username/password (and optional TOTP secret) on the
Settings -> Fidelity tab.
"""
import datetime as dt

from sqlalchemy import select

from ..config import DATA_DIR
from ..db import Account, Position, SessionLocal
from ..secrets_store import store

# Shared browser-session file: data/Fidelity_riskmon.json. The interactive login
# (fidelity_login.py) and the headless refresh both pin to this so a one-time
# login (approving 2FA once) is reused by later headless refreshes.
SESSION_TITLE = "riskmon"


class FidelityApiUnavailable(Exception):
    """Raised when the API path can't complete; the caller should fall back to CSV."""


def configured() -> bool:
    return bool(
        (store.get("fidelity_username") or "").strip()
        and (store.get("fidelity_password") or "").strip()
    )


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _csv_cost_basis() -> tuple[dict, dict]:
    """The API omits cost basis, so read average cost/share from the latest
    Fidelity CSV. Returns (by_account_symbol, by_symbol) -> avg cost per share."""
    from . import sync as csv_sync

    path = csv_sync.find_csv()
    if path is None:
        return {}, {}
    import csv as _csv

    by_acct: dict[tuple[str, str], float] = {}
    by_symbol: dict[str, float] = {}
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in _csv.DictReader(fh):
                symbol = csv_sync._clean_symbol(row.get("Symbol"))
                if not symbol:
                    continue
                cbp = csv_sync._parse_money(row.get("Average cost basis"))
                if cbp is None:  # derive per-share from total / quantity if blank
                    cbv = csv_sync._parse_money(row.get("Cost basis total"))
                    qty = csv_sync._parse_qty(row.get("Quantity"))
                    cbp = (cbv / qty) if (cbv is not None and qty) else None
                if cbp is None:
                    continue
                acct_num = (row.get("Account number") or "").strip()
                if acct_num:
                    by_acct[(acct_num, symbol)] = cbp
                by_symbol.setdefault(symbol, cbp)
    except OSError:
        return {}, {}
    return by_acct, by_symbol


def sync_via_api() -> dict:
    username = (store.get("fidelity_username") or "").strip()
    password = (store.get("fidelity_password") or "").strip()
    totp_secret = (store.get("fidelity_totp_secret") or "").strip()
    if not username or not password:
        raise FidelityApiUnavailable("Fidelity API credentials are not configured")

    try:
        from fidelity import fidelity as fidelity_lib
    except Exception as exc:  # not installed / import error
        raise FidelityApiUnavailable(
            f"fidelity-api not available ({exc}); run `pip install fidelity-api` and `playwright install`"
        ) from exc

    browser = None
    try:
        browser = fidelity_lib.FidelityAutomation(
            headless=True, save_state=True,
            profile_path=str(DATA_DIR), title=SESSION_TITLE,
        )
        _step_1, step_2 = browser.login(
            username=username, password=password,
            totp_secret=(totp_secret or None), save_device=True,
        )
        if not step_2:
            # Headless can't clear an interactive 2FA challenge (e.g. Duo push).
            # The saved session has expired or there's no usable TOTP secret.
            raise FidelityApiUnavailable(
                "Fidelity needs a fresh login/2FA — run `python fidelity_login.py` "
                "to re-establish the saved session (or add a TOTP secret)"
            )
        account_info = browser.getAccountInfo()
    except FidelityApiUnavailable:
        raise
    except Exception as exc:  # any Playwright/login/scrape failure
        raise FidelityApiUnavailable(f"Fidelity API sync failed ({exc})") from exc
    finally:
        if browser is not None:
            try:
                browser.close_browser()
            except Exception:
                pass

    if not account_info:
        raise FidelityApiUnavailable("Fidelity API returned no accounts")

    now = dt.datetime.utcnow()
    cb_by_acct, cb_by_symbol = _csv_cost_basis()
    method = "Fidelity API (CSV basis)" if (cb_by_acct or cb_by_symbol) else "Fidelity API"
    positions_seen = 0
    accounts_seen = 0
    with SessionLocal() as session:
        for raw_num, info in account_info.items():
            acct_num = str(raw_num).strip()
            if not acct_num or not isinstance(info, dict):
                continue
            account_hash = f"fidelity-{acct_num}"
            account = session.scalar(select(Account).where(Account.account_hash == account_hash))
            if account is None:
                account = Account(
                    broker="fidelity",
                    account_hash=account_hash,
                    account_number_masked=f"...{acct_num[-4:]}" if len(acct_num) > 4 else acct_num,
                    account_type=(info.get("nickname") or None),
                )
                session.add(account)
                session.flush()
            elif info.get("nickname"):
                account.account_type = info.get("nickname")

            for old in list(account.positions):
                session.delete(old)
            session.flush()

            for stock in info.get("stocks") or []:
                if not isinstance(stock, dict):
                    continue
                ticker = str(stock.get("ticker") or "").strip()
                qty = _to_float(stock.get("quantity"))
                if not ticker or qty is None:
                    continue
                # Cost basis is merged from the latest CSV (the API omits it):
                # average cost/share, preferring an exact account+symbol match,
                # applied to the current API quantity.
                cbp = cb_by_acct.get((acct_num, ticker))
                if cbp is None:
                    cbp = cb_by_symbol.get(ticker)
                session.add(Position(
                    account_id=account.id,
                    symbol=ticker,
                    description=None,
                    asset_type=None,
                    quantity=qty,
                    market_value=_to_float(stock.get("value")),
                    last_price=_to_float(stock.get("last_price")),
                    cost_basis_price=cbp,
                    cost_basis_value=(cbp * qty) if cbp is not None else None,
                ))
                positions_seen += 1

            account.last_synced_at = now
            account.sync_method = method
            accounts_seen += 1
        session.commit()

    return {
        "positions": positions_seen,
        "accounts": accounts_seen,
        "method": method,
        "synced_at": now,
    }
