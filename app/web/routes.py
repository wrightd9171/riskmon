import datetime as dt
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from ..coinbase.client import CoinbaseAuthError
from ..coinbase.sync import sync_all as coinbase_sync_all
from ..db import Account, BitcoinLoan, Position, SessionLocal, init_db
from .. import scheduler as digest_scheduler
from ..db import ChainAddress
from ..fidelity import sync as fidelity_sync
from ..onchain import sync as onchain_sync
from ..robinhood.client import RobinhoodAuthError, generate_keypair, verify_connection
from ..robinhood.sync import sync_all as robinhood_sync_all
from ..schwab import oauth as schwab_oauth
from ..schwab.client import TokenError
from ..schwab.sync import sync_all as schwab_sync_all
from ..strike.client import StrikeAuthError
from ..strike.sync import sync_all as strike_sync_all
from ..secrets_store import store
from ..refresh import get_status as refresh_get_status, start_refresh

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _num(value, decimals: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:,.{decimals}f}"


def _qty(value) -> str:
    if value is None:
        return "—"
    return f"{value:,.0f}"


def _btc(value) -> str:
    if value is None:
        return "—"
    return f"{value:,.8f}"


def _qty_btc(value) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}"


def _price(value) -> str:
    if value is None:
        return "—"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def _ago(value) -> str:
    """Relative age of a UTC datetime, e.g. 'just now', '4m ago', '6h ago', '5d ago'."""
    if value is None:
        return "never synced"
    secs = max(0.0, (dt.datetime.utcnow() - value).total_seconds())
    if secs < 90:
        return "just now"
    if secs < 90 * 60:
        return f"{int(round(secs / 60))}m ago"
    if secs < 36 * 3600:
        return f"{int(round(secs / 3600))}h ago"
    return f"{int(round(secs / 86400))}d ago"


def _age_level(value) -> str:
    """Bucket for coloring: fresh (<1d), warn (<1w), old (older or never)."""
    if value is None:
        return "old"
    hrs = (dt.datetime.utcnow() - value).total_seconds() / 3600
    if hrs < 24:
        return "fresh"
    if hrs < 24 * 7:
        return "warn"
    return "old"


TEMPLATES.env.filters["num"] = _num
TEMPLATES.env.filters["qty"] = _qty
TEMPLATES.env.filters["btc"] = _btc
TEMPLATES.env.filters["qty_btc"] = _qty_btc
TEMPLATES.env.filters["price"] = _price
TEMPLATES.env.filters["ago"] = _ago
TEMPLATES.env.filters["age_level"] = _age_level

router = APIRouter()


def _gate(request: Request) -> Optional[RedirectResponse]:
    if not store.is_initialized():
        if request.url.path != "/setup":
            return RedirectResponse("/setup", status_code=303)
    elif not store.is_unlocked():
        if request.url.path != "/unlock":
            return RedirectResponse("/unlock", status_code=303)
    return None


def _account_label(account: Account) -> str:
    if account.broker == "coinbase":
        return "Coinbase"
    if account.broker == "robinhood":
        return "Robinhood"
    if account.broker == "strike":
        return "Strike"
    if account.broker == "fidelity":
        return f"Fidelity {account.account_type}" if account.account_type else "Fidelity"
    if account.broker == "onchain":
        return account.account_type or "On-chain"
    parts = ["Schwab"]
    if account.account_type:
        parts.append(account.account_type.title().replace("_", " "))
    if account.account_number_masked:
        parts.append(account.account_number_masked)
    return " ".join(parts)


BROKER_METHOD = {
    "schwab": "Schwab API (OAuth)",
    "coinbase": "Coinbase API",
    "robinhood": "Robinhood API",
    "strike": "Strike API",
    "onchain": "mempool.space",
    "fidelity": "Fidelity",
}


def _sync_method(account: Account) -> str:
    """How the account's data was last obtained. Fidelity records the actual
    path (API vs CSV); the others always come from their own API."""
    return account.sync_method or BROKER_METHOD.get(account.broker) or account.broker


PROVIDER_ORDER = ["schwab", "coinbase", "robinhood", "strike", "fidelity", "onchain"]
PROVIDER_LABEL = {
    "schwab": "Schwab", "coinbase": "Coinbase", "robinhood": "Robinhood",
    "strike": "Strike", "fidelity": "Fidelity", "onchain": "On-chain",
}


def _data_sources(accounts) -> list[dict]:
    """One freshness entry per provider for the Positions header — Fidelity's
    sub-accounts collapse into a single 'Fidelity' entry (they sync together)."""
    groups: dict[str, dict] = {}
    for a in accounts:
        g = groups.get(a.broker)
        if g is None:
            groups[a.broker] = {
                "label": PROVIDER_LABEL.get(a.broker, a.broker.title()),
                "synced": a.last_synced_at,
                "method": _sync_method(a),
            }
        elif a.last_synced_at and (g["synced"] is None or a.last_synced_at > g["synced"]):
            g["synced"] = a.last_synced_at
    ordered = [groups[b] for b in PROVIDER_ORDER if b in groups]
    ordered += [g for b, g in groups.items() if b not in PROVIDER_ORDER]
    return ordered


def _any_broker_configured() -> bool:
    return bool(
        store.get("refresh_token")
        or store.get("coinbase_key_name")
        or store.get("robinhood_api_key")
        or store.get("strike_api_key")
        or fidelity_sync.available()
        or onchain_sync.has_addresses()
    )


def _current_btc_price(session) -> float | None:
    stmt = select(Position).where(Position.symbol == "BTC", Position.last_price.is_not(None))
    for pos in session.execute(stmt).scalars():
        if pos.last_price:
            return float(pos.last_price)
    return None


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    gate = _gate(request)
    if gate:
        return gate
    if not _any_broker_configured():
        return RedirectResponse("/connect", status_code=303)
    return RedirectResponse("/main", status_code=303)


@router.get("/setup", response_class=HTMLResponse)
def setup_get(request: Request):
    if store.is_initialized():
        return RedirectResponse("/unlock", status_code=303)
    return TEMPLATES.TemplateResponse(request, "setup.html", {"error": None})


@router.post("/setup", response_class=HTMLResponse)
def setup_post(
    request: Request,
    password: str = Form(...),
    confirm: str = Form(...),
    client_id: str = Form(...),
    client_secret: str = Form(...),
):
    if store.is_initialized():
        return RedirectResponse("/unlock", status_code=303)
    if password != confirm:
        return TEMPLATES.TemplateResponse(
            request, "setup.html", {"error": "Passwords do not match."}, status_code=400,
        )
    if len(password) < 8:
        return TEMPLATES.TemplateResponse(
            request, "setup.html", {"error": "Password must be at least 8 characters."}, status_code=400,
        )
    store.initialize(password, {
        "client_id": client_id.strip(),
        "client_secret": client_secret.strip(),
    })
    init_db()
    return RedirectResponse("/connect", status_code=303)


@router.get("/unlock", response_class=HTMLResponse)
def unlock_get(request: Request):
    if not store.is_initialized():
        return RedirectResponse("/setup", status_code=303)
    if store.is_unlocked():
        return RedirectResponse("/", status_code=303)
    return TEMPLATES.TemplateResponse(request, "unlock.html", {"error": None})


@router.post("/unlock", response_class=HTMLResponse)
def unlock_post(request: Request, password: str = Form(...)):
    if not store.is_initialized():
        return RedirectResponse("/setup", status_code=303)
    if not store.unlock(password):
        return TEMPLATES.TemplateResponse(
            request, "unlock.html", {"error": "Incorrect password."}, status_code=401,
        )
    init_db()
    return RedirectResponse("/", status_code=303)


@router.post("/lock")
def lock_post():
    store.lock()
    return RedirectResponse("/unlock", status_code=303)


SETTINGS_TAB_KEYS = {"schwab", "coinbase", "robinhood", "strike", "fidelity", "onchain", "notify"}


def _settings_full_ctx(request: Request, overrides: dict | None = None) -> dict:
    """Build the context for every Settings panel at once. `overrides` is
    {section: {field: value}} used to inject a per-section error/message."""
    overrides = overrides or {}
    with SessionLocal() as session:
        addrs = session.execute(
            select(ChainAddress).order_by(ChainAddress.chain, ChainAddress.id)
        ).scalars().all()
        addresses = [
            {"id": a.id, "chain": a.chain, "address": a.address, "label": a.label or ""}
            for a in addrs
        ]
    csv = fidelity_sync.find_csv()
    rk = store.get("robinhood_api_key") or ""
    ctx = {
        "schwab": {"status": schwab_oauth.status(), "connected": bool(store.get("refresh_token"))},
        "coinbase": {"connected": bool(store.get("coinbase_key_name")),
                     "key_name": store.get("coinbase_key_name") or "", "error": None},
        "robinhood": {"connected": bool(rk),
                      "api_key_preview": (rk[:6] + "…" + rk[-4:]) if len(rk) > 12 else rk,
                      "public_key": store.get("robinhood_public_key") or "",
                      "api_key": "", "error": None, "message": None},
        "strike": {"connected": bool(store.get("strike_api_key")), "error": None},
        "fidelity": {
            "csv": csv.name if csv else None,
            "username": store.get("fidelity_username") or "",
            "password_set": bool(store.get("fidelity_password")),
            "totp_set": bool(store.get("fidelity_totp_secret")),
        },
        "onchain": {"addresses": addresses},
        "notify": {
            "enabled": bool(store.get("notify_enabled")),
            "token_set": bool(store.get("pushover_token")),
            "user_set": bool(store.get("pushover_user_key")),
            "last_sent": store.get("notify_last_sent") or "",
            "error": None, "message": None,
        },
    }
    for section, vals in overrides.items():
        ctx[section].update(vals)
    return ctx


def _render_settings(request: Request, active_tab: str = "schwab",
                     status_code: int = 200, **overrides) -> HTMLResponse:
    ctx = _settings_full_ctx(request, overrides)
    ctx["active_tab"] = active_tab if active_tab in SETTINGS_TAB_KEYS else "schwab"
    return TEMPLATES.TemplateResponse(request, "settings.html", ctx, status_code=status_code)


@router.get("/settings", response_class=HTMLResponse)
def settings_get(request: Request, tab: str = Query(default="schwab")):
    gate = _gate(request)
    if gate:
        return gate
    return _render_settings(request, tab)


@router.get("/connect", response_class=HTMLResponse)
def connect_get(request: Request):
    gate = _gate(request)
    if gate:
        return gate
    return RedirectResponse("/settings?tab=schwab", status_code=303)


@router.get("/fidelity", response_class=HTMLResponse)
def fidelity_get(request: Request):
    gate = _gate(request)
    if gate:
        return gate
    return RedirectResponse("/settings?tab=fidelity", status_code=303)


@router.post("/fidelity")
def fidelity_post(
    request: Request,
    fidelity_username: str = Form(""),
    fidelity_password: str = Form(""),
    fidelity_totp_secret: str = Form(""),
):
    gate = _gate(request)
    if gate:
        return gate
    updates = {}
    if fidelity_username.strip():
        updates["fidelity_username"] = fidelity_username.strip()
    if fidelity_password.strip():
        updates["fidelity_password"] = fidelity_password.strip()
    if fidelity_totp_secret.strip():
        updates["fidelity_totp_secret"] = fidelity_totp_secret.strip()
    if updates:
        store.update(**updates)
    return RedirectResponse("/settings?tab=fidelity", status_code=303)


@router.post("/fidelity/disconnect")
def fidelity_disconnect():
    if not store.is_unlocked():
        return RedirectResponse("/unlock", status_code=303)
    store.update(fidelity_username=None, fidelity_password=None, fidelity_totp_secret=None)
    return RedirectResponse("/settings?tab=fidelity", status_code=303)


@router.post("/connect")
def connect_post(request: Request):
    gate = _gate(request)
    if gate:
        return gate
    try:
        schwab_oauth.start_callback_server()
        auth_url = schwab_oauth.build_authorize_url()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return RedirectResponse(auth_url, status_code=303)


@router.get("/connect/status")
def connect_status():
    return schwab_oauth.status()


SORT_KEYS = {
    "symbol": lambda x: ((x.get("symbol") or "").lower(),),
    "description": lambda x: ((x.get("description") or "").lower(),),
    "last_price": lambda x: (x.get("last_price") is None, x.get("last_price") or 0),
    "quantity": lambda x: (x.get("quantity") is None, x.get("quantity") or 0),
    "market_value": lambda x: (x.get("market_value") is None, x.get("market_value") or 0),
    "cost_basis_price": lambda x: (x.get("cost_basis_price") is None, x.get("cost_basis_price") or 0),
    "cost_basis_value": lambda x: (x.get("cost_basis_value") is None, x.get("cost_basis_value") or 0),
    "unrealized_pnl": lambda x: (x.get("unrealized_pnl") is None, x.get("unrealized_pnl") or 0),
}
STRING_SORT_COLS = {"symbol", "description"}


@router.get("/main", response_class=HTMLResponse)
def main_view(
    request: Request,
    acct: list[int] = Query(default=[]),
    sort: str = Query(default="market_value"),
    dir: str = Query(default="desc"),
    include_zero: int = Query(default=0),
    hide_no_pnl: int = Query(default=0),
):
    gate = _gate(request)
    if gate:
        return gate
    if not _any_broker_configured():
        return RedirectResponse("/connect", status_code=303)

    selected_ids = set(acct) if acct else set()
    if sort not in SORT_KEYS:
        sort = "market_value"
    if dir not in ("asc", "desc"):
        dir = "desc"

    with SessionLocal() as session:
        all_accounts = session.execute(
            select(Account).order_by(Account.account_type, Account.account_number_masked)
        ).scalars().all()

        stmt = select(Position, Account).join(Account, Position.account_id == Account.id)
        if selected_ids:
            stmt = stmt.where(Account.id.in_(selected_ids))
        rows = session.execute(stmt).all()

        agg: dict[str, dict] = defaultdict(lambda: {
            "symbol": "",
            "description": "",
            "quantity": 0.0,
            "market_value": 0.0,
            "cost_basis_value": 0.0,
            "last_price": None,
            "has_basis": False,
            "per_account": [],
        })
        for pos, acct_row in rows:
            key = "CASH" if _is_cash(pos.symbol) else pos.symbol
            a = agg[key]
            a["symbol"] = key
            if key == "CASH":
                a["description"] = "Cash and money markets"
            elif pos.description:
                a["description"] = pos.description
            a["quantity"] += pos.quantity or 0
            a["market_value"] += pos.market_value or 0
            if pos.cost_basis_value is not None:
                a["cost_basis_value"] += pos.cost_basis_value
                a["has_basis"] = True
            if a["last_price"] is None and pos.last_price is not None:
                a["last_price"] = pos.last_price
            per_pnl = (
                (pos.market_value - pos.cost_basis_value)
                if pos.market_value is not None and pos.cost_basis_value is not None
                else None
            )
            a["per_account"].append({
                "account_label": _account_label(acct_row),
                "quantity": pos.quantity,
                "market_value": pos.market_value,
                "last_price": pos.last_price,
                "cost_basis_price": pos.cost_basis_price,
                "cost_basis_value": pos.cost_basis_value,
                "unrealized_pnl": per_pnl,
            })

        loans_rows = session.execute(select(BitcoinLoan)).scalars().all()
        if loans_rows and not selected_ids:
            total_collateral = sum(l.collateral_btc or 0 for l in loans_rows)
            total_debt = sum(
                (l.outstanding_principal or 0) + (l.interest_accrued or 0)
                for l in loans_rows
            )
            btc_price = _current_btc_price(session)

            if (total_collateral > 0 or total_debt > 0):
                if "BTC" not in agg:
                    agg["BTC"] = {
                        "symbol": "BTC",
                        "description": "Bitcoin",
                        "quantity": 0.0,
                        "market_value": 0.0,
                        "cost_basis_value": 0.0,
                        "last_price": btc_price,
                        "per_account": [],
                    }
                btc_entry = agg["BTC"]
                if btc_entry["last_price"] is None:
                    btc_entry["last_price"] = btc_price

                if btc_price:
                    debt_in_btc = total_debt / btc_price
                    equity_btc = total_collateral - debt_in_btc
                    equity_mv = equity_btc * btc_price
                    btc_entry["per_account"].append({
                        "account_label": "Strike Loan Equity",
                        "quantity": equity_btc,
                        "market_value": equity_mv,
                        "last_price": btc_price,
                        "cost_basis_price": None,
                        "cost_basis_value": None,
                        "unrealized_pnl": None,
                    })
                    btc_entry["quantity"] += equity_btc
                    btc_entry["market_value"] += equity_mv
                elif total_collateral > 0:
                    btc_entry["per_account"].append({
                        "account_label": "Strike Loan Equity",
                        "quantity": total_collateral,
                        "market_value": None,
                        "last_price": None,
                        "cost_basis_price": None,
                        "cost_basis_value": None,
                    })
                    btc_entry["quantity"] += total_collateral

        for a in agg.values():
            if a["quantity"] and a["market_value"] is not None:
                common_price = a["market_value"] / a["quantity"]
                a["last_price"] = common_price
                for p in a["per_account"]:
                    p["last_price"] = common_price
            if not a["has_basis"]:
                a["cost_basis_value"] = None
                a["cost_basis_price"] = None
            else:
                a["cost_basis_price"] = (
                    a["cost_basis_value"] / a["quantity"] if a["quantity"] else None
                )
            a["unrealized_pnl"] = (
                (a["market_value"] - a["cost_basis_value"])
                if a["market_value"] is not None and a["cost_basis_value"] is not None
                else None
            )

        symbols = list(agg.values())
        if not include_zero:
            symbols = [s for s in symbols if round(s.get("market_value") or 0) != 0]
        if hide_no_pnl:
            symbols = [s for s in symbols if s.get("unrealized_pnl") is not None]

        symbols.sort(key=SORT_KEYS[sort], reverse=(dir == "desc"))

        total_market_value = sum(s.get("market_value") or 0 for s in symbols)
        total_cost_basis_value = sum(s.get("cost_basis_value") or 0 for s in symbols)
        total_unrealized_pnl = sum(
            s["unrealized_pnl"] for s in symbols if s.get("unrealized_pnl") is not None
        )

        base_params: list[tuple[str, str]] = [("acct", str(a)) for a in sorted(selected_ids)]
        if include_zero:
            base_params.append(("include_zero", "1"))
        if hide_no_pnl:
            base_params.append(("hide_no_pnl", "1"))

        def sort_link(col: str) -> str:
            if col == sort:
                new_dir = "asc" if dir == "desc" else "desc"
            else:
                new_dir = "asc" if col in STRING_SORT_COLS else "desc"
            params = list(base_params) + [("sort", col), ("dir", new_dir)]
            return "/main?" + urlencode(params)

        toggle_zero_params = list(base_params) + [("sort", sort), ("dir", dir)]
        if include_zero:
            toggle_zero_params = [p for p in toggle_zero_params if p[0] != "include_zero"]
        else:
            toggle_zero_params.append(("include_zero", "1"))
        toggle_zero_url = "/main?" + urlencode(toggle_zero_params)

        toggle_pnl_params = list(base_params) + [("sort", sort), ("dir", dir)]
        if hide_no_pnl:
            toggle_pnl_params = [p for p in toggle_pnl_params if p[0] != "hide_no_pnl"]
        else:
            toggle_pnl_params.append(("hide_no_pnl", "1"))
        toggle_pnl_url = "/main?" + urlencode(toggle_pnl_params)

        # Clearing the account filter keeps the current sort and toggles.
        clear_params = [("sort", sort), ("dir", dir)]
        if include_zero:
            clear_params.append(("include_zero", "1"))
        if hide_no_pnl:
            clear_params.append(("hide_no_pnl", "1"))
        clear_url = "/main?" + urlencode(clear_params)

        return TEMPLATES.TemplateResponse(request, "main.html", {
            "clear_url": clear_url,
            "symbols": symbols,
            "accounts": [
                {"id": a.id, "label": _account_label(a), "synced": a.last_synced_at}
                for a in all_accounts
            ],
            "data_sources": _data_sources(all_accounts),
            "selected_ids": selected_ids,
            "sort_by": sort,
            "sort_dir": dir,
            "include_zero": bool(include_zero),
            "sort_link": sort_link,
            "toggle_zero_url": toggle_zero_url,
            "toggle_pnl_url": toggle_pnl_url,
            "hide_no_pnl": bool(hide_no_pnl),
            "total_market_value": total_market_value,
            "total_cost_basis_value": total_cost_basis_value,
            "total_unrealized_pnl": total_unrealized_pnl,
        })


@router.post("/refresh")
def refresh_post():
    if not store.is_unlocked():
        return RedirectResponse("/unlock", status_code=303)
    if not _any_broker_configured():
        return RedirectResponse("/settings", status_code=303)
    start_refresh()  # no-op if a refresh is already running
    return RedirectResponse("/refreshing", status_code=303)


@router.get("/refreshing", response_class=HTMLResponse)
def refreshing_get(request: Request):
    gate = _gate(request)
    if gate:
        return gate
    return TEMPLATES.TemplateResponse(request, "refreshing.html", {"status": refresh_get_status()})


@router.get("/refresh/status")
def refresh_status():
    if not store.is_unlocked():
        return JSONResponse({"running": False, "sources": [], "locked": True})
    return JSONResponse(refresh_get_status())


@router.get("/coinbase", response_class=HTMLResponse)
def coinbase_get(request: Request):
    gate = _gate(request)
    if gate:
        return gate
    return RedirectResponse("/settings?tab=coinbase", status_code=303)


@router.post("/coinbase", response_class=HTMLResponse)
def coinbase_post(
    request: Request,
    creds_json: str = Form(default=""),
    key_name: str = Form(default=""),
    private_key: str = Form(default=""),
):
    gate = _gate(request)
    if gate:
        return gate

    parsed_name = key_name.strip()
    parsed_key = private_key.strip()

    if creds_json.strip():
        try:
            blob = json.loads(creds_json)
            parsed_name = (blob.get("name") or parsed_name).strip()
            parsed_key = (blob.get("privateKey") or parsed_key).strip()
        except json.JSONDecodeError as exc:
            return _render_settings(
                request, active_tab="coinbase", status_code=400,
                coinbase={"error": f"Invalid JSON: {exc}"},
            )

    parsed_key = parsed_key.replace("\\n", "\n").replace("\r\n", "\n")

    if not parsed_name or not parsed_key:
        return _render_settings(
            request, active_tab="coinbase", status_code=400,
            coinbase={"error": "Both key name and private key are required."},
        )

    store.update(
        coinbase_key_name=parsed_name,
        coinbase_private_key=parsed_key,
    )
    return RedirectResponse("/settings?tab=coinbase", status_code=303)


@router.post("/coinbase/disconnect")
def coinbase_disconnect():
    if not store.is_unlocked():
        return RedirectResponse("/unlock", status_code=303)
    store.update(coinbase_key_name=None, coinbase_private_key=None)
    return RedirectResponse("/settings?tab=coinbase", status_code=303)


@router.get("/robinhood", response_class=HTMLResponse)
def robinhood_get(request: Request):
    gate = _gate(request)
    if gate:
        return gate
    return RedirectResponse("/settings?tab=robinhood", status_code=303)


@router.post("/robinhood/keygen", response_class=HTMLResponse)
def robinhood_keygen(request: Request):
    gate = _gate(request)
    if gate:
        return gate
    _private_b64, public_b64 = generate_keypair()
    store.update(robinhood_private_key=_private_b64, robinhood_public_key=public_b64)
    return _render_settings(
        request, active_tab="robinhood",
        robinhood={"message": (
            "New key pair generated. Register the public key with Robinhood, "
            "then paste the API key it issues below."
        )},
    )


@router.post("/robinhood", response_class=HTMLResponse)
def robinhood_post(request: Request, api_key: str = Form(...)):
    gate = _gate(request)
    if gate:
        return gate
    key = api_key.strip()
    if not key:
        return _render_settings(
            request, active_tab="robinhood", status_code=400,
            robinhood={"error": "API key is required."},
        )
    if not store.get("robinhood_private_key"):
        return _render_settings(
            request, active_tab="robinhood", status_code=400,
            robinhood={"error": "Generate a key pair first, then register its public key with Robinhood."},
        )
    store.update(robinhood_api_key=key)
    try:
        verify_connection()
    except RobinhoodAuthError as exc:
        store.update(robinhood_api_key=None)
        return _render_settings(
            request, active_tab="robinhood", status_code=400,
            robinhood={"error": str(exc)},
        )
    return RedirectResponse("/settings?tab=robinhood", status_code=303)


@router.post("/robinhood/disconnect")
def robinhood_disconnect():
    if not store.is_unlocked():
        return RedirectResponse("/unlock", status_code=303)
    store.update(
        robinhood_api_key=None,
        robinhood_private_key=None,
        robinhood_public_key=None,
    )
    return RedirectResponse("/settings?tab=robinhood", status_code=303)


@router.get("/strike", response_class=HTMLResponse)
def strike_get(request: Request):
    gate = _gate(request)
    if gate:
        return gate
    return RedirectResponse("/settings?tab=strike", status_code=303)


@router.post("/strike", response_class=HTMLResponse)
def strike_post(request: Request, api_key: str = Form(...)):
    gate = _gate(request)
    if gate:
        return gate
    key = api_key.strip()
    if not key:
        return _render_settings(
            request, active_tab="strike", status_code=400,
            strike={"error": "API key is required."},
        )
    store.update(strike_api_key=key)
    return RedirectResponse("/settings?tab=strike", status_code=303)


@router.post("/strike/disconnect")
def strike_disconnect():
    if not store.is_unlocked():
        return RedirectResponse("/unlock", status_code=303)
    store.update(strike_api_key=None)
    return RedirectResponse("/settings?tab=strike", status_code=303)


DAYS_OF_WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@router.get("/notify", response_class=HTMLResponse)
def notify_get(request: Request):
    gate = _gate(request)
    if gate:
        return gate
    return RedirectResponse("/settings?tab=notify", status_code=303)


@router.post("/notify")
def notify_post(
    request: Request,
    enabled: str = Form(""),
    pushover_token: str = Form(""),
    pushover_user_key: str = Form(""),
):
    gate = _gate(request)
    if gate:
        return gate
    updates = {"notify_enabled": bool(enabled)}
    if pushover_token.strip():
        updates["pushover_token"] = pushover_token.strip()
    if pushover_user_key.strip():
        updates["pushover_user_key"] = pushover_user_key.strip()
    store.update(**updates)
    return RedirectResponse("/settings?tab=notify", status_code=303)


@router.post("/notify/test")
def notify_test(request: Request):
    gate = _gate(request)
    if gate:
        return gate
    try:
        digest_scheduler.send_digest_now()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Send failed: {exc}")
    return RedirectResponse("/settings?tab=notify", status_code=303)


@router.get("/onchain", response_class=HTMLResponse)
def onchain_get(request: Request):
    gate = _gate(request)
    if gate:
        return gate
    return RedirectResponse("/settings?tab=onchain", status_code=303)


@router.post("/onchain")
def onchain_post(
    request: Request,
    chain: str = Form("BTC"),
    address: str = Form(...),
    label: str = Form(""),
):
    gate = _gate(request)
    if gate:
        return gate
    chain = chain.strip().upper() or "BTC"
    address = address.strip()
    label = label.strip() or None
    if not address:
        return RedirectResponse("/settings?tab=onchain", status_code=303)
    with SessionLocal() as session:
        existing = session.scalar(
            select(ChainAddress).where(ChainAddress.address == address)
        )
        if existing:
            existing.chain = chain
            existing.label = label
        else:
            session.add(ChainAddress(chain=chain, address=address, label=label))
        session.commit()
    return RedirectResponse("/settings?tab=onchain", status_code=303)


@router.post("/onchain/{addr_id}/delete")
def onchain_delete(request: Request, addr_id: int):
    gate = _gate(request)
    if gate:
        return gate
    with SessionLocal() as session:
        addr = session.get(ChainAddress, addr_id)
        if addr is not None:
            session.delete(addr)
            session.commit()
    return RedirectResponse("/settings?tab=onchain", status_code=303)


IBIT_OPTION_PATTERN = re.compile(r"^IBIT\d")

CASH_SYMBOLS = frozenset({"USD", "SPAXX", "FDRXX", "VMRXX"})


def _is_cash(symbol: str) -> bool:
    if not symbol:
        return False
    return symbol.rstrip("*") in CASH_SYMBOLS


def _category(name: str, rows: list[dict]) -> dict:
    subtotal_btc = sum(i["virtual_btc"] for i in rows if i["virtual_btc"] is not None)
    subtotal_mv = sum(i["market_value"] for i in rows if i["market_value"] is not None)
    return {
        "name": name,
        "rows": rows,
        "subtotal_btc": subtotal_btc,
        "subtotal_mv": subtotal_mv,
    }


CRYPTO_SORT_KEYS = {
    "label": lambda x: ((x.get("label") or "").lower(),),
    "raw_symbol": lambda x: ((x.get("raw_symbol") or "").lower(),),
    "raw_qty": lambda x: (x.get("raw_qty") is None, x.get("raw_qty") or 0),
    "virtual_btc": lambda x: (x.get("virtual_btc") is None, x.get("virtual_btc") or 0),
    "market_value": lambda x: (x.get("market_value") is None, x.get("market_value") or 0),
}
CRYPTO_STRING_COLS = {"label", "raw_symbol"}

# These columns reorder the category blocks themselves, not just rows within
# them. Summable columns sort by subtotal; symbol sorts by the block's
# representative (alphabetically-first) symbol, since each block is essentially
# one instrument (Direct BTC → BTC, BTC ETF → IBIT). Native qty has no
# meaningful cross-instrument aggregate, so it leaves the semantic order intact.
CRYPTO_CAT_SORT_KEYS = {
    "raw_symbol": lambda c: (
        min((r.get("raw_symbol") or "" for r in c["rows"]), default="").lower(),
    ),
    "virtual_btc": lambda c: (c.get("subtotal_btc") is None, c.get("subtotal_btc") or 0),
    "market_value": lambda c: (c.get("subtotal_mv") is None, c.get("subtotal_mv") or 0),
}


@router.get("/crypto", response_class=HTMLResponse)
def crypto_view(
    request: Request,
    include_all: int = Query(default=0),
    sort: str = Query(default="market_value"),
    dir: str = Query(default="desc"),
):
    gate = _gate(request)
    if gate:
        return gate
    if sort not in CRYPTO_SORT_KEYS:
        sort = "market_value"
    if dir not in ("asc", "desc"):
        dir = "desc"
    with SessionLocal() as session:
        btc_price = _current_btc_price(session)

        rows = session.execute(
            select(Position, Account).join(Account, Position.account_id == Account.id)
        ).all()

        direct: list[dict] = []
        etf: list[dict] = []
        long_options: list[dict] = []
        other: list[dict] = []

        for pos, acct in rows:
            symbol = pos.symbol
            qty = pos.quantity or 0
            mv = pos.market_value or 0
            label = _account_label(acct)

            if symbol == "BTC":
                actual_mv = qty * btc_price if btc_price else mv
                direct.append({
                    "label": label,
                    "raw_symbol": "BTC",
                    "raw_qty": qty,
                    "virtual_btc": qty,
                    "market_value": actual_mv,
                })
            elif symbol == "IBIT":
                virtual = mv / btc_price if btc_price else None
                etf.append({
                    "label": label,
                    "raw_symbol": "IBIT",
                    "raw_qty": qty,
                    "virtual_btc": virtual,
                    "market_value": mv,
                })
            elif IBIT_OPTION_PATTERN.match(symbol) and qty > 0:
                virtual = mv / btc_price if btc_price else None
                long_options.append({
                    "label": label,
                    "raw_symbol": symbol,
                    "raw_qty": qty,
                    "virtual_btc": virtual,
                    "market_value": mv,
                })
            elif not IBIT_OPTION_PATTERN.match(symbol) and round(mv) != 0:
                virtual = mv / btc_price if btc_price else None
                other.append({
                    "label": label,
                    "raw_symbol": symbol,
                    "raw_qty": qty,
                    "virtual_btc": virtual,
                    "market_value": mv,
                })

        loans_rows = session.execute(select(BitcoinLoan)).scalars().all()
        loan_equity_btc = None
        loan_equity_mv = None
        if loans_rows and btc_price:
            total_collateral = sum(l.collateral_btc or 0 for l in loans_rows)
            total_debt = sum(
                (l.outstanding_principal or 0) + (l.interest_accrued or 0)
                for l in loans_rows
            )
            debt_btc = total_debt / btc_price
            loan_equity_btc = total_collateral - debt_btc
            loan_equity_mv = loan_equity_btc * btc_price

        categories: list[dict] = []
        if direct:
            categories.append(_category("Direct BTC", direct))
        if etf:
            categories.append(_category("BTC ETF (IBIT)", etf))
        if long_options:
            categories.append(_category("Long IBIT Options", long_options))
        if loan_equity_btc is not None and loan_equity_btc != 0:
            categories.append({
                "name": "Strike Loan Equity",
                "rows": [{
                    "label": "Bitcoin-backed loans (collateral − debt)",
                    "raw_symbol": "",
                    "raw_qty": None,
                    "virtual_btc": loan_equity_btc,
                    "market_value": loan_equity_mv,
                }],
                "subtotal_btc": loan_equity_btc,
                "subtotal_mv": loan_equity_mv,
            })
        if include_all and other:
            categories.append(_category("Other Positions", other))

        # Sort rows within each category, then reorder the category blocks
        # themselves by their subtotal when sorting on a summable column.
        for cat in categories:
            cat["rows"].sort(key=CRYPTO_SORT_KEYS[sort], reverse=(dir == "desc"))
        if sort in CRYPTO_CAT_SORT_KEYS:
            categories.sort(key=CRYPTO_CAT_SORT_KEYS[sort], reverse=(dir == "desc"))

        total_btc = sum(c["subtotal_btc"] for c in categories if c["subtotal_btc"] is not None)
        total_mv = sum(c["subtotal_mv"] for c in categories if c["subtotal_mv"] is not None)

        def sort_link(col: str) -> str:
            if col == sort:
                new_dir = "asc" if dir == "desc" else "desc"
            else:
                new_dir = "asc" if col in CRYPTO_STRING_COLS else "desc"
            params: list[tuple[str, str]] = []
            if include_all:
                params.append(("include_all", "1"))
            params.extend([("sort", col), ("dir", new_dir)])
            return "/crypto?" + urlencode(params)

        toggle_params = [("sort", sort), ("dir", dir)]
        if not include_all:
            toggle_params.insert(0, ("include_all", "1"))
        toggle_all_url = "/crypto?" + urlencode(toggle_params)

        return TEMPLATES.TemplateResponse(request, "crypto.html", {
            "categories": categories,
            "total_btc": total_btc if categories else None,
            "total_mv": total_mv if categories else None,
            "btc_price": btc_price,
            "include_all": bool(include_all),
            "toggle_all_url": toggle_all_url,
            "sort_by": sort,
            "sort_dir": dir,
            "sort_link": sort_link,
        })


LOAN_SORT_KEYS = {
    "origination_date": lambda x: (x["origination_date"] is None, x["origination_date"]),
    "termination_date": lambda x: (x["termination_date"] is None, x["termination_date"]),
    "principal": lambda x: (x["principal"] is None, x["principal"] or 0),
    "interest": lambda x: (x["interest"] is None, x["interest"] or 0),
    "debt_btc": lambda x: (x["debt_btc"] is None, x["debt_btc"] or 0),
    "collateral": lambda x: (x["collateral"] is None, x["collateral"] or 0),
    "net_btc": lambda x: (x["net_btc"] is None, x["net_btc"] or 0),
    "collateral_value": lambda x: (x["collateral_value"] is None, x["collateral_value"] or 0),
    "net_mv": lambda x: (x["net_mv"] is None, x["net_mv"] or 0),
    "ltv": lambda x: (x["ltv"] is None, x["ltv"] or 0),
    "notes": lambda x: ((x["notes"] or "").lower(),),
}
# Columns that read most naturally ascending on first click (dates chronological,
# notes alphabetical); everything else defaults to descending.
LOAN_ASC_DEFAULT_COLS = {"notes", "origination_date", "termination_date"}


@router.get("/loans", response_class=HTMLResponse)
def loans_view(
    request: Request,
    sort: str = Query(default="origination_date"),
    dir: str = Query(default="asc"),
):
    gate = _gate(request)
    if gate:
        return gate
    if sort not in LOAN_SORT_KEYS:
        sort = "origination_date"
    if dir not in ("asc", "desc"):
        dir = "asc"
    with SessionLocal() as session:
        loans = session.execute(
            select(BitcoinLoan).order_by(BitcoinLoan.origination_date)
        ).scalars().all()
        btc_price = _current_btc_price(session)

        rows = []
        agg_principal = 0.0
        agg_interest = 0.0
        agg_collateral = 0.0
        agg_collateral_value = 0.0
        for loan in loans:
            collateral_value = loan.collateral_btc * btc_price if btc_price else None
            debt = (loan.outstanding_principal or 0) + (loan.interest_accrued or 0)
            debt_btc = debt / btc_price if btc_price else None
            net_btc = (loan.collateral_btc or 0) - debt_btc if debt_btc is not None else None
            net_mv = net_btc * btc_price if net_btc is not None and btc_price else None
            ltv = debt / collateral_value if collateral_value else None
            rows.append({
                "id": loan.id,
                "origination_date": loan.origination_date,
                "termination_date": loan.termination_date,
                "principal": loan.outstanding_principal,
                "interest": loan.interest_accrued,
                "debt_btc": debt_btc,
                "collateral": loan.collateral_btc,
                "net_btc": net_btc,
                "collateral_value": collateral_value,
                "net_mv": net_mv,
                "ltv": ltv,
                "notes": loan.notes,
            })
            agg_principal += loan.outstanding_principal or 0
            agg_interest += loan.interest_accrued or 0
            agg_collateral += loan.collateral_btc or 0
            if collateral_value is not None:
                agg_collateral_value += collateral_value

        agg_debt = agg_principal + agg_interest
        agg_debt_btc = agg_debt / btc_price if btc_price else None
        agg_net_btc = agg_collateral - agg_debt_btc if agg_debt_btc is not None else None
        agg_net_mv = agg_net_btc * btc_price if agg_net_btc is not None and btc_price else None
        agg_ltv = agg_debt / agg_collateral_value if agg_collateral_value else None

        rows.sort(key=LOAN_SORT_KEYS[sort], reverse=(dir == "desc"))

        def sort_link(col: str) -> str:
            if col == sort:
                new_dir = "asc" if dir == "desc" else "desc"
            else:
                new_dir = "asc" if col in LOAN_ASC_DEFAULT_COLS else "desc"
            return "/loans?" + urlencode([("sort", col), ("dir", new_dir)])

        return TEMPLATES.TemplateResponse(request, "loans.html", {
            "loans": rows,
            "sort_by": sort,
            "sort_dir": dir,
            "sort_link": sort_link,
            "btc_price": btc_price,
            "agg": {
                "principal": agg_principal,
                "interest": agg_interest,
                "debt_btc": agg_debt_btc,
                "collateral": agg_collateral,
                "net_btc": agg_net_btc,
                "collateral_value": agg_collateral_value or None,
                "net_mv": agg_net_mv,
                "ltv": agg_ltv,
            },
        })


@router.get("/loans/new", response_class=HTMLResponse)
def loan_new_get(request: Request):
    gate = _gate(request)
    if gate:
        return gate
    return TEMPLATES.TemplateResponse(request, "loan_edit.html", {
        "loan": None,
        "error": None,
    })


@router.post("/loans/new")
def loan_new_post(
    request: Request,
    origination_date: str = Form(...),
    termination_date: str = Form(...),
    outstanding_principal: float = Form(...),
    interest_accrued: float = Form(0.0),
    collateral_btc: float = Form(...),
    notes: str = Form(""),
):
    gate = _gate(request)
    if gate:
        return gate
    try:
        orig = dt.date.fromisoformat(origination_date)
        term = dt.date.fromisoformat(termination_date)
    except ValueError as exc:
        return TEMPLATES.TemplateResponse(request, "loan_edit.html", {
            "loan": None, "error": f"Invalid date: {exc}",
        }, status_code=400)

    with SessionLocal() as session:
        session.add(BitcoinLoan(
            origination_date=orig,
            termination_date=term,
            outstanding_principal=outstanding_principal,
            interest_accrued=interest_accrued,
            collateral_btc=collateral_btc,
            notes=notes.strip() or None,
        ))
        session.commit()
    return RedirectResponse("/loans", status_code=303)


@router.get("/loans/{loan_id}/edit", response_class=HTMLResponse)
def loan_edit_get(request: Request, loan_id: int):
    gate = _gate(request)
    if gate:
        return gate
    with SessionLocal() as session:
        loan = session.get(BitcoinLoan, loan_id)
        if loan is None:
            raise HTTPException(status_code=404, detail="Loan not found")
        return TEMPLATES.TemplateResponse(request, "loan_edit.html", {
            "loan": loan,
            "error": None,
        })


@router.post("/loans/{loan_id}/edit")
def loan_edit_post(
    request: Request,
    loan_id: int,
    origination_date: str = Form(...),
    termination_date: str = Form(...),
    outstanding_principal: float = Form(...),
    interest_accrued: float = Form(0.0),
    collateral_btc: float = Form(...),
    notes: str = Form(""),
):
    gate = _gate(request)
    if gate:
        return gate
    try:
        orig = dt.date.fromisoformat(origination_date)
        term = dt.date.fromisoformat(termination_date)
    except ValueError as exc:
        with SessionLocal() as session:
            loan = session.get(BitcoinLoan, loan_id)
        return TEMPLATES.TemplateResponse(request, "loan_edit.html", {
            "loan": loan, "error": f"Invalid date: {exc}",
        }, status_code=400)

    with SessionLocal() as session:
        loan = session.get(BitcoinLoan, loan_id)
        if loan is None:
            raise HTTPException(status_code=404, detail="Loan not found")
        loan.origination_date = orig
        loan.termination_date = term
        loan.outstanding_principal = outstanding_principal
        loan.interest_accrued = interest_accrued
        loan.collateral_btc = collateral_btc
        loan.notes = notes.strip() or None
        session.commit()
    return RedirectResponse("/loans", status_code=303)


@router.post("/loans/{loan_id}/delete")
def loan_delete(request: Request, loan_id: int):
    gate = _gate(request)
    if gate:
        return gate
    with SessionLocal() as session:
        loan = session.get(BitcoinLoan, loan_id)
        if loan is not None:
            session.delete(loan)
            session.commit()
    return RedirectResponse("/loans", status_code=303)
