import datetime as dt

from sqlalchemy import select

from ..db import Account, Position, SessionLocal
from .client import get_prices, list_holdings

ACCOUNT_HASH = "robinhood-default"


def sync_all() -> dict:
    holdings = list_holdings()

    assets: list[tuple[str, float]] = []
    for h in holdings:
        code = (h.get("asset_code") or "").upper()
        try:
            quantity = float(h.get("total_quantity") or 0)
        except (TypeError, ValueError):
            continue
        if code and quantity:
            assets.append((code, quantity))

    symbols = [f"{code}-USD" for code, _ in assets if code != "USD"]
    prices = get_prices(symbols) if symbols else {}

    now = dt.datetime.utcnow()
    positions_seen = 0

    with SessionLocal() as session:
        account = session.scalar(
            select(Account).where(Account.account_hash == ACCOUNT_HASH)
        )
        if account is None:
            account = Account(
                broker="robinhood",
                account_hash=ACCOUNT_HASH,
                account_number_masked=None,
                account_type=None,
            )
            session.add(account)
            session.flush()

        for old in list(account.positions):
            session.delete(old)
        session.flush()

        for code, quantity in assets:
            if code == "USD":
                last_price = 1.0
                market_value = quantity
            else:
                last_price = prices.get(code)
                market_value = last_price * quantity if last_price is not None else None

            session.add(Position(
                account_id=account.id,
                symbol=code,
                description=f"Robinhood {code}",
                asset_type="CASH" if code == "USD" else "CRYPTO",
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
