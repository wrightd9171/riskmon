import datetime as dt

from sqlalchemy import select

from ..db import Account, ChainAddress, Position, SessionLocal
from .client import get_btc_address_balance, get_btc_usd_price


def has_addresses() -> bool:
    with SessionLocal() as s:
        return s.scalar(select(ChainAddress.id).limit(1)) is not None


def sync_all() -> dict:
    now = dt.datetime.utcnow()
    with SessionLocal() as session:
        addresses = session.execute(select(ChainAddress)).scalars().all()
        if not addresses:
            return {"positions": 0, "accounts": 0, "synced_at": None}

        by_chain: dict[str, list[ChainAddress]] = {}
        for a in addresses:
            by_chain.setdefault(a.chain, []).append(a)

        positions_seen = 0
        accounts_touched: list[str] = []
        errors: list[str] = []

        for chain, addr_list in by_chain.items():
            if chain != "BTC":
                errors.append(f"Chain {chain} not yet supported (only BTC)")
                continue

            total_btc = 0.0
            for addr in addr_list:
                try:
                    total_btc += get_btc_address_balance(addr.address)
                except Exception as exc:
                    errors.append(f"Failed {addr.address[:12]}…: {exc}")

            btc_price = get_btc_usd_price()
            market_value = total_btc * btc_price if btc_price else None

            account_hash = f"onchain-{chain}"
            account = session.scalar(
                select(Account).where(Account.account_hash == account_hash)
            )
            if account is None:
                account = Account(
                    broker="onchain",
                    account_hash=account_hash,
                    account_type=f"On-chain {chain}",
                )
                session.add(account)
                session.flush()

            for old in list(account.positions):
                session.delete(old)
            session.flush()

            session.add(Position(
                account_id=account.id,
                symbol=chain,
                description=f"On-chain {chain}",
                asset_type="CRYPTO",
                quantity=total_btc,
                market_value=market_value,
                last_price=btc_price,
                cost_basis_price=None,
                cost_basis_value=None,
            ))
            positions_seen += 1
            account.last_synced_at = now
            accounts_touched.append(chain)

        session.commit()

    return {
        "positions": positions_seen,
        "accounts": len(accounts_touched),
        "synced_at": now,
        "errors": errors,
    }
