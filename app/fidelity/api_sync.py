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

from ..db import Account, Position, SessionLocal
from ..secrets_store import store


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
        browser = fidelity_lib.FidelityAutomation(headless=True, save_state=True)
        _step_1, step_2 = browser.login(username=username, password=password, save_device=True)
        if not step_2:
            # 2FA needed. Can only proceed unattended if we have a TOTP secret.
            if not totp_secret:
                raise FidelityApiUnavailable(
                    "Fidelity requires 2FA and no TOTP secret is configured"
                )
            try:
                import pyotp
                code = pyotp.TOTP(totp_secret).now()
            except Exception as exc:
                raise FidelityApiUnavailable(f"could not generate a 2FA code ({exc})") from exc
            if browser.login_2FA(code) is False:
                raise FidelityApiUnavailable("Fidelity rejected the 2FA code")
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
                session.add(Position(
                    account_id=account.id,
                    symbol=ticker,
                    description=None,
                    asset_type=None,
                    quantity=qty,
                    market_value=_to_float(stock.get("value")),
                    last_price=_to_float(stock.get("last_price")),
                    cost_basis_price=None,   # the API does not expose cost basis
                    cost_basis_value=None,
                ))
                positions_seen += 1

            account.last_synced_at = now
            account.sync_method = "Fidelity API"
            accounts_seen += 1
        session.commit()

    return {
        "positions": positions_seen,
        "accounts": accounts_seen,
        "method": "Fidelity API",
        "synced_at": now,
    }
