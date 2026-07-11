import base64
import binascii
import secrets as py_secrets
import time
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from ..secrets_store import store

API_BASE = "https://api.coinbase.com"
API_HOST = "api.coinbase.com"


class CoinbaseAuthError(Exception):
    pass


def _get_creds() -> tuple[str, str]:
    key_name = store.get("coinbase_key_name")
    private_key = store.get("coinbase_private_key")
    if not key_name or not private_key:
        raise CoinbaseAuthError("Coinbase API key not configured")
    private_key = private_key.replace("\\n", "\n").replace("\r\n", "\n")
    return key_name, private_key


def _load_key(private_key_material: str):
    text = private_key_material.strip()
    if "-----BEGIN" in text:
        try:
            return load_pem_private_key(text.encode("utf-8"), password=None)
        except Exception as exc:
            raise CoinbaseAuthError(f"PEM key could not be parsed: {exc}") from exc

    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CoinbaseAuthError(
            f"Private key is neither PEM nor base64: {exc}"
        ) from exc

    if len(raw) == 64:
        return Ed25519PrivateKey.from_private_bytes(raw[:32])
    if len(raw) == 32:
        return Ed25519PrivateKey.from_private_bytes(raw)
    raise CoinbaseAuthError(
        f"Unexpected raw key length {len(raw)} bytes (expected 32 or 64 for Ed25519)."
    )


def _build_jwt(method: str, path: str) -> str:
    key_name, private_key_pem = _get_creds()
    key = _load_key(private_key_pem)
    if isinstance(key, Ed25519PrivateKey):
        algorithm = "EdDSA"
    elif isinstance(key, EllipticCurvePrivateKey):
        algorithm = "ES256"
    else:
        raise CoinbaseAuthError(
            f"Unsupported Coinbase key type: {type(key).__name__}"
        )
    now = int(time.time())
    payload = {
        "sub": key_name,
        "iss": "cdp",
        "nbf": now,
        "exp": now + 120,
        "uri": f"{method} {API_HOST}{path}",
    }
    headers = {"kid": key_name, "nonce": py_secrets.token_hex(16)}
    return jwt.encode(payload, key, algorithm=algorithm, headers=headers)


def _get(path: str, params: dict | None = None) -> Any:
    token = _build_jwt("GET", path)
    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{API_BASE}{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            params=params or {},
        )
    resp.raise_for_status()
    return resp.json()


def list_accounts() -> list[dict]:
    all_accounts: list[dict] = []
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {"limit": 250}
        if cursor:
            params["cursor"] = cursor
        result = _get("/api/v3/brokerage/accounts", params=params)
        all_accounts.extend(result.get("accounts") or [])
        if not result.get("has_next"):
            break
        cursor = result.get("cursor")
        if not cursor:
            break
    return all_accounts


def list_products() -> list[dict]:
    result = _get("/api/v3/brokerage/products", params={"product_type": "SPOT"})
    return result.get("products") or []
