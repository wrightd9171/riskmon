import time
from typing import Any

import httpx

from ..config import CALLBACK_URL, SCHWAB_API_BASE, SCHWAB_TOKEN_URL
from ..secrets_store import store

TOKEN_REFRESH_MARGIN_SECS = 60


class TokenError(Exception):
    pass


def _basic_auth() -> tuple[str, str]:
    client_id = store.get("client_id")
    client_secret = store.get("client_secret")
    if not client_id or not client_secret:
        raise TokenError("Schwab client credentials not configured")
    return client_id, client_secret


def exchange_code(code: str) -> dict:
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            SCHWAB_TOKEN_URL,
            auth=_basic_auth(),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": CALLBACK_URL,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code >= 400:
        raise TokenError(f"Code exchange failed: {resp.status_code} {resp.text}")
    return _normalize_token_response(resp.json())


def refresh_tokens() -> dict:
    refresh_token = store.get("refresh_token")
    if not refresh_token:
        raise TokenError("No refresh token stored")
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            SCHWAB_TOKEN_URL,
            auth=_basic_auth(),
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code >= 400:
        raise TokenError(f"Refresh failed: {resp.status_code} {resp.text}")
    return _normalize_token_response(resp.json())


def _normalize_token_response(body: dict) -> dict:
    now = int(time.time())
    return {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token") or store.get("refresh_token"),
        "expires_at": now + int(body.get("expires_in", 1800)),
        "token_type": body.get("token_type", "Bearer"),
        "scope": body.get("scope"),
    }


def save_tokens(tokens: dict) -> None:
    store.update(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        expires_at=tokens["expires_at"],
        token_type=tokens.get("token_type", "Bearer"),
    )


def get_access_token() -> str:
    access_token = store.get("access_token")
    expires_at = store.get("expires_at", 0)
    if not access_token or (expires_at - TOKEN_REFRESH_MARGIN_SECS) <= time.time():
        tokens = refresh_tokens()
        save_tokens(tokens)
        access_token = tokens["access_token"]
    return access_token


def _get(path: str, params: dict | None = None) -> Any:
    token = get_access_token()
    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{SCHWAB_API_BASE}{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            params=params or {},
        )
    resp.raise_for_status()
    return resp.json()


def list_account_numbers() -> list[dict]:
    return _get("/accounts/accountNumbers")


def list_accounts_with_positions() -> list[dict]:
    return _get("/accounts", params={"fields": "positions"})
