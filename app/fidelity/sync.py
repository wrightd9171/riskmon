import csv
import datetime as dt
from pathlib import Path

from sqlalchemy import select

from ..config import DATA_DIR
from ..db import Account, Position, SessionLocal


def _parse_money(value):
    if value is None:
        return None
    s = value.strip().strip('"').strip()
    if not s or s in ("--", "-"):
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    s = s.replace("$", "").replace(",", "").strip()
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if negative else v


def _parse_qty(value):
    if value is None:
        return None
    s = value.strip().strip('"').strip()
    if not s or s in ("--", "-"):
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1]
    s = s.replace(",", "").strip()
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if negative else v


def _clean_symbol(raw):
    s = (raw or "").strip()
    if s.startswith("-"):
        s = s[1:].strip()
    return s


def find_csv() -> Path | None:
    matches = sorted(DATA_DIR.glob("Fidelity_*.csv"), reverse=True)
    return matches[0] if matches else None


def has_csv() -> bool:
    return find_csv() is not None


def available() -> bool:
    """True if Fidelity can be synced at all — API creds configured or a CSV present."""
    from .api_sync import configured
    return configured() or has_csv()


def sync_all() -> dict:
    """Refresh Fidelity: try the fidelity-api scraper first, fall back to the CSV."""
    from .api_sync import FidelityApiUnavailable, configured, sync_via_api

    api_error = None
    if configured():
        try:
            return sync_via_api()
        except FidelityApiUnavailable as exc:
            api_error = str(exc)

    if has_csv():
        result = sync_from_csv()
        if api_error:
            result["api_error"] = api_error
        return result

    return {"positions": 0, "accounts": 0, "csv": None,
            "method": None, "synced_at": None, "api_error": api_error}


def sync_from_csv() -> dict:
    csv_path = find_csv()
    if csv_path is None:
        return {"positions": 0, "accounts": 0, "csv": None, "method": None, "synced_at": None}

    now = dt.datetime.utcnow()
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    accounts_touched: dict[str, Account] = {}
    positions_seen = 0

    with SessionLocal() as session:
        for row in rows:
            acct_num = (row.get("Account number") or "").strip()
            acct_name = (row.get("Account name") or "").strip()
            if not acct_num or acct_num in accounts_touched:
                continue

            account_hash = f"fidelity-{acct_num}"
            account = session.scalar(
                select(Account).where(Account.account_hash == account_hash)
            )
            if account is None:
                account = Account(
                    broker="fidelity",
                    account_hash=account_hash,
                    account_number_masked=f"...{acct_num[-4:]}" if len(acct_num) > 4 else acct_num,
                    account_type=acct_name,
                )
                session.add(account)
                session.flush()
            elif acct_name:
                account.account_type = acct_name

            for old in list(account.positions):
                session.delete(old)
            session.flush()
            accounts_touched[acct_num] = account

        for row in rows:
            acct_num = (row.get("Account number") or "").strip()
            account = accounts_touched.get(acct_num)
            if account is None:
                continue

            symbol = _clean_symbol(row.get("Symbol"))
            description = (row.get("Description") or "").strip()

            if not symbol and description.upper() == "BROKERAGELINK":
                continue

            if not symbol:
                symbol = description or "UNKNOWN"

            quantity = _parse_qty(row.get("Quantity"))
            last_price = _parse_money(row.get("Last price"))
            market_value = _parse_money(row.get("Current value"))
            cost_basis_value = _parse_money(row.get("Cost basis total"))
            cost_basis_price = _parse_money(row.get("Average cost basis"))

            if quantity is None and market_value is not None:
                quantity = market_value
                if last_price is None:
                    last_price = 1.0

            if quantity is None:
                continue

            session.add(Position(
                account_id=account.id,
                symbol=symbol,
                description=description,
                asset_type=(row.get("Type") or "").strip() or None,
                quantity=quantity,
                market_value=market_value,
                last_price=last_price,
                cost_basis_price=cost_basis_price,
                cost_basis_value=cost_basis_value,
            ))
            positions_seen += 1

        for account in accounts_touched.values():
            account.last_synced_at = now
            account.sync_method = "Fidelity CSV"
        session.commit()

    return {
        "positions": positions_seen,
        "accounts": len(accounts_touched),
        "csv": csv_path.name,
        "method": "Fidelity CSV",
        "synced_at": now,
    }
