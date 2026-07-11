import httpx

MEMPOOL_BASE = "https://mempool.space/api"


class OnchainError(Exception):
    pass


def get_btc_address_balance(address: str) -> float:
    """Return confirmed BTC balance for a Bitcoin address."""
    with httpx.Client(timeout=30) as client:
        resp = client.get(f"{MEMPOOL_BASE}/address/{address}")
    if resp.status_code == 400:
        raise OnchainError(f"Invalid Bitcoin address: {address}")
    resp.raise_for_status()
    data = resp.json()
    stats = data.get("chain_stats") or {}
    sats = (stats.get("funded_txo_sum") or 0) - (stats.get("spent_txo_sum") or 0)
    return sats / 100_000_000


def get_btc_usd_price() -> float | None:
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(f"{MEMPOOL_BASE}/v1/prices")
        resp.raise_for_status()
        usd = (resp.json() or {}).get("USD")
        return float(usd) if usd else None
    except Exception:
        return None
