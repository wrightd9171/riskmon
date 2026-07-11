import datetime as dt

from sqlalchemy import select

from ..db import Account, Position, SessionLocal
from .client import list_account_numbers, list_accounts_with_positions


def sync_all() -> dict:
    numbers = list_account_numbers()
    num_map = {n["accountNumber"]: n for n in numbers}
    data = list_accounts_with_positions()
    now = dt.datetime.utcnow()

    accounts_seen: list[str] = []
    positions_seen = 0

    with SessionLocal() as session:
        for entry in data:
            sec = entry.get("securitiesAccount") or entry
            account_number = str(sec.get("accountNumber") or "")
            hash_value = (num_map.get(account_number) or {}).get("hashValue") or sec.get("hashValue")
            if not hash_value:
                continue
            account_type = sec.get("type")
            masked = f"...{account_number[-4:]}" if account_number else None

            account = session.scalar(
                select(Account).where(Account.account_hash == hash_value)
            )
            if account is None:
                account = Account(
                    broker="schwab",
                    account_hash=hash_value,
                    account_number_masked=masked,
                    account_type=account_type,
                )
                session.add(account)
                session.flush()
            else:
                if masked:
                    account.account_number_masked = masked
                if account_type:
                    account.account_type = account_type

            for old in list(account.positions):
                session.delete(old)
            session.flush()

            for pos in sec.get("positions") or []:
                inst = pos.get("instrument") or {}
                symbol = inst.get("symbol") or inst.get("cusip") or "UNKNOWN"
                description = inst.get("description")
                asset_type = inst.get("assetType") or inst.get("type")
                long_qty = float(pos.get("longQuantity") or 0)
                short_qty = float(pos.get("shortQuantity") or 0)
                quantity = long_qty - short_qty
                market_value = pos.get("marketValue")
                avg_price = pos.get("averagePrice")

                last_price = None
                if quantity and market_value is not None:
                    last_price = market_value / quantity

                cost_basis_value = None
                if avg_price is not None and quantity:
                    cost_basis_value = avg_price * quantity

                session.add(Position(
                    account_id=account.id,
                    symbol=symbol,
                    description=description,
                    asset_type=asset_type,
                    quantity=quantity,
                    market_value=market_value,
                    last_price=last_price,
                    cost_basis_price=avg_price,
                    cost_basis_value=cost_basis_value,
                ))
                positions_seen += 1

            account.last_synced_at = now
            accounts_seen.append(hash_value)

        session.commit()

    return {"accounts": len(accounts_seen), "positions": positions_seen, "synced_at": now}
