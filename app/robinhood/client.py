import base64
import binascii
import time
from typing import Any

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ..secrets_store import store

# Robinhood Crypto Trading API. Read-only usage only: this module issues GET
# requests exclusively (account, holdings, quotes) and has no order path.
# Robinhood does not offer a read-only key scope, so this is enforced here.
API_BASE = "https://trading.robinhood.com"


class RobinhoodAuthError(Exception):
    pass


def generate_keypair() -> tuple[str, str]:
    """Create an Ed25519 keypair. Returns (private_key_b64, public_key_b64).

    The user registers the public key with Robinhood's API Credentials Portal;
    Robinhood then issues an API key that pairs with it.
    """
    private_key = Ed25519PrivateKey.generate()
    seed = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(seed).decode("ascii"), base64.b64encode(public).decode("ascii")


def _load_private_key() -> Ed25519PrivateKey:
    b64 = store.get("robinhood_private_key")
    if not b64:
        raise RobinhoodAuthError("Robinhood private key not configured")
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RobinhoodAuthError(f"Private key is not valid base64: {exc}") from exc
    if len(raw) == 64:  # some tools store seed + public key concatenated
        raw = raw[:32]
    if len(raw) != 32:
        raise RobinhoodAuthError(
            f"Unexpected private key length {len(raw)} bytes (expected 32 for Ed25519)."
        )
    return Ed25519PrivateKey.from_private_bytes(raw)


def _api_key() -> str:
    key = store.get("robinhood_api_key")
    if not key:
        raise RobinhoodAuthError("Robinhood API key not configured")
    return key


def _auth_headers(method: str, path: str, body: str = "") -> dict[str, str]:
    api_key = _api_key()
    private_key = _load_private_key()
    timestamp = int(time.time())
    message = f"{api_key}{timestamp}{path}{method}{body}".encode("utf-8")
    signature = private_key.sign(message)
    return {
        "x-api-key": api_key,
        "x-signature": base64.b64encode(signature).decode("utf-8"),
        "x-timestamp": str(timestamp),
        "Accept": "application/json",
    }


def _get(path: str) -> Any:
    # `path` must include any query string verbatim — the same string is signed
    # and sent, so it must not be re-encoded by the client.
    headers = _auth_headers("GET", path)
    with httpx.Client(timeout=30) as client:
        resp = client.get(f"{API_BASE}{path}", headers=headers)
    resp.raise_for_status()
    return resp.json()


def get_account() -> dict:
    return _get("/api/v1/crypto/trading/accounts/")


def list_holdings() -> list[dict]:
    data = _get("/api/v1/crypto/trading/holdings/")
    return data.get("results") or []


def get_prices(symbols: list[str]) -> dict[str, float]:
    """Return {asset_code: usd_price} for the given trading-pair symbols
    (e.g. ["BTC-USD", "ETH-USD"]). Priced off the best bid/ask mark."""
    if not symbols:
        return {}
    query = "&".join(f"symbol={s}" for s in symbols)
    data = _get(f"/api/v1/crypto/marketdata/best_bid_ask/?{query}")
    prices: dict[str, float] = {}
    for row in data.get("results") or []:
        symbol = row.get("symbol") or ""
        asset_code = symbol.split("-")[0]
        if not asset_code:
            continue
        price = row.get("price")
        if price is None:
            bid = row.get("bid_inclusive_of_sell_spread")
            ask = row.get("ask_inclusive_of_buy_spread")
            if bid is not None and ask is not None:
                try:
                    price = (float(bid) + float(ask)) / 2
                except (TypeError, ValueError):
                    price = None
        if price is None:
            continue
        try:
            prices[asset_code] = float(price)
        except (TypeError, ValueError):
            continue
    return prices


def verify_connection() -> None:
    """Raise RobinhoodAuthError if credentials can't reach the account endpoint."""
    try:
        resp = get_account()
    except httpx.HTTPStatusError as exc:
        raise RobinhoodAuthError(
            f"Robinhood rejected the credentials ({exc.response.status_code}). "
            "Check the API key and that the registered public key matches."
        ) from exc
    if isinstance(resp, dict) and resp.get("errors"):
        detail = resp["errors"][0].get("detail") if resp["errors"] else "unknown error"
        raise RobinhoodAuthError(f"Robinhood API error: {detail}")
