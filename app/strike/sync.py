import datetime as dt

from sqlalchemy import select

from ..db import Account, Position, SessionLocal
from .client import get_btc_usd_rate, list_balances

ACCOUNT_HASH = "strike-default"


def sync_all() -> dict:
    balances = list_balances()
    btc_rate = get_btc_usd_rate()
    now = dt.datetime.utcnow()
    positions_seen = 0

    with SessionLocal() as session:
        account = session.scalar(
            select(Account).where(Account.account_hash == ACCOUNT_HASH)
        )
        if account is None:
            account = Account(
                broker="strike",
                account_hash=ACCOUNT_HASH,
                account_number_masked=None,
                account_type=None,
            )
            session.add(account)
            session.flush()

        for old in list(account.positions):
            session.delete(old)
        session.flush()

        for b in balances:
            if not isinstance(b, dict):
                continue
            currency = (b.get("currency") or "").upper()
            if not currency:
                continue
            amount_str: str | None = None
            for field in ("current", "total", "available", "amount"):
                v = b.get(field)
                if isinstance(v, dict):
                    v = v.get("amount")
                if isinstance(v, (str, int, float)) and v not in (None, ""):
                    amount_str = str(v)
                    break
            if amount_str is None:
                continue
            try:
                quantity = float(amount_str)
            except (TypeError, ValueError):
                continue
            if quantity == 0:
                continue

            if currency == "USD":
                last_price = 1.0
                market_value = quantity
            elif currency == "BTC":
                last_price = btc_rate
                market_value = last_price * quantity if last_price is not None else None
            else:
                last_price = None
                market_value = None

            session.add(Position(
                account_id=account.id,
                symbol=currency,
                description=f"Strike {currency}",
                asset_type="CRYPTO" if currency == "BTC" else "CASH",
                quantity=quantity,
                market_value=market_value,
                last_price=last_price,
                cost_basis_price=None,
                cost_basis_value=None,
            ))
            positions_seen += 1

        account.last_synced_at = now
        session.commit()

    return {"positions": positions_seen, "synced_at": now}
