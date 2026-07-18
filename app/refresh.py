"""Refresh all connected price sources.

Shared by the web Refresh button and the scheduled digest so the daily
day-over-day P&L is computed against fresh prices. Never raises — returns a
list of human-readable error strings (empty on full success).
"""
from .secrets_store import store


def refresh_all() -> list[str]:
    errors: list[str] = []

    from .coinbase.sync import sync_all as coinbase_sync_all
    from .fidelity import sync as fidelity_sync
    from .onchain import sync as onchain_sync
    from .robinhood.sync import sync_all as robinhood_sync_all
    from .schwab.sync import sync_all as schwab_sync_all
    from .strike.sync import sync_all as strike_sync_all

    if store.get("refresh_token"):
        try:
            schwab_sync_all()
        except Exception as exc:
            errors.append(f"Schwab: {exc}")

    if store.get("coinbase_key_name"):
        try:
            coinbase_sync_all()
        except Exception as exc:
            errors.append(f"Coinbase: {exc}")

    if store.get("robinhood_api_key"):
        try:
            robinhood_sync_all()
        except Exception as exc:
            errors.append(f"Robinhood: {exc}")

    if store.get("strike_api_key"):
        try:
            strike_sync_all()
        except Exception as exc:
            errors.append(f"Strike: {exc}")

    if fidelity_sync.available():
        try:
            fidelity_sync.sync_all()
        except Exception as exc:
            errors.append(f"Fidelity: {exc}")

    if onchain_sync.has_addresses():
        try:
            result = onchain_sync.sync_all()
            for e in result.get("errors") or []:
                errors.append(f"On-chain: {e}")
        except Exception as exc:
            errors.append(f"On-chain: {exc}")

    return errors
