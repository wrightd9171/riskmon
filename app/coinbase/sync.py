import datetime as dt

from sqlalchemy import select

from ..db import Account, Position, SessionLocal
from .client import list_accounts, list_products

ACCOUNT_HASH = "coinbase-default"


def sync_all() -> dict:
    accounts_raw = list_accounts()
    products_raw = list_products()

    prices: dict[str, float] = {}
    for p in products_raw:
        product_id = p.get("product_id", "")
        if not product_id.endswith("-USD"):
            continue
        currency = product_id[:-len("-USD")]
        price_str = p.get("price")
        if price_str:
            try:
                prices[currency] = float(price_str)
            except (TypeError, ValueError):
                pass

    now = dt.datetime.utcnow()
    positions_seen = 0

    with SessionLocal() as session:
        account = session.scalar(
            select(Account).where(Account.account_hash == ACCOUNT_HASH)
        )
        if account is None:
            account = Account(
                broker="coinbase",
                account_hash=ACCOUNT_HASH,
                account_number_masked=None,
                account_type=None,
            )
            session.add(account)
            session.flush()

        for old in list(account.positions):
            session.delete(old)
        session.flush()

        for a in accounts_raw:
            if not a.get("active", True):
                continue
            currency = a.get("currency") or ""
            if not currency:
                continue
            available = (a.get("available_balance") or {}).get("value", "0")
            held = (a.get("hold") or {}).get("value", "0")
            try:
                quantity = float(available) + float(held)
            except (TypeError, ValueError):
                continue
            if quantity == 0:
                continue

            if currency == "USD":
                last_price = 1.0
                market_value = quantity
            else:
                last_price = prices.get(currency)
                market_value = last_price * quantity if last_price is not None else None

            session.add(Position(
                account_id=account.id,
                symbol=currency,
                description=a.get("name") or currency,
                asset_type="CRYPTO",
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
