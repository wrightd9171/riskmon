from typing import Any

import httpx

from ..secrets_store import store

API_BASE = "https://api.strike.me/v1"


class StrikeAuthError(Exception):
    pass


def _api_key() -> str:
    key = store.get("strike_api_key")
    if not key:
        raise StrikeAuthError("Strike API key not configured")
    return key


def _get(path: str, params: dict | None = None) -> Any:
    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{API_BASE}{path}",
            headers={
                "Authorization": f"Bearer {_api_key()}",
                "Accept": "application/json",
            },
            params=params or {},
        )
    resp.raise_for_status()
    return resp.json()


def list_balances() -> list[dict]:
    data = _get("/balances")
    return data if isinstance(data, list) else []


def get_btc_usd_rate() -> float | None:
    try:
        data = _get("/rates/ticker")
    except Exception:
        return None
    for entry in data or []:
        if entry.get("sourceCurrency") == "BTC" and entry.get("targetCurrency") == "USD":
            try:
                return float(entry.get("amount") or 0)
            except (TypeError, ValueError):
                continue
    return None
