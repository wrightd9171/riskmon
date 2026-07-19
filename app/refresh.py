"""Refresh all connected price sources.

Two entry points:
- refresh_all(): synchronous, returns error strings. Used by the scheduled
  digest (send_digest.py), where there is no UI.
- start_refresh()/get_status(): runs the same syncs in a background thread and
  tracks per-source progress so the web UI can show a live "Refreshing..." view
  (and surface a Schwab reconnect inline instead of silently redirecting).
"""
import datetime as dt
import threading

from .secrets_store import store


def refresh_all() -> list[str]:
    """Synchronous refresh; returns human-readable error strings (empty on success)."""
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


# --- Background refresh with per-source progress (for the web UI) ---

_lock = threading.Lock()
_state: dict = {"running": False, "sources": [], "started_at": None, "done_at": None}
_thread: threading.Thread | None = None


def configured_sources() -> list[tuple[str, str]]:
    """The (key, label) of every source that is set up, in refresh order."""
    from .fidelity import sync as fidelity_sync
    from .onchain import sync as onchain_sync

    srcs: list[tuple[str, str]] = []
    if store.get("refresh_token"):
        srcs.append(("schwab", "Schwab"))
    if store.get("coinbase_key_name"):
        srcs.append(("coinbase", "Coinbase"))
    if store.get("robinhood_api_key"):
        srcs.append(("robinhood", "Robinhood"))
    if store.get("strike_api_key"):
        srcs.append(("strike", "Strike"))
    if fidelity_sync.available():
        srcs.append(("fidelity", "Fidelity"))
    if onchain_sync.has_addresses():
        srcs.append(("onchain", "On-chain"))
    return srcs


def get_status() -> dict:
    with _lock:
        return {
            "running": _state["running"],
            "started_at": _state["started_at"],
            "done_at": _state["done_at"],
            "sources": [dict(s) for s in _state["sources"]],
        }


def _set_source(key: str, **fields) -> None:
    with _lock:
        for s in _state["sources"]:
            if s["key"] == key:
                s.update(fields)
                return


def _run_one(key: str) -> tuple[str, str]:
    """Run one source. Returns (state, detail) — state is ok | error | reconnect."""
    import httpx

    from .schwab.client import TokenError
    try:
        if key == "schwab":
            from .schwab.sync import sync_all as schwab_sync_all
            try:
                schwab_sync_all()
            except TokenError:
                return ("reconnect", "login expired — reconnect Schwab")
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401, 403):
                    return ("reconnect", "login expired — reconnect Schwab")
                raise
        elif key == "coinbase":
            from .coinbase.sync import sync_all as coinbase_sync_all
            coinbase_sync_all()
        elif key == "robinhood":
            from .robinhood.sync import sync_all as robinhood_sync_all
            robinhood_sync_all()
        elif key == "strike":
            from .strike.sync import sync_all as strike_sync_all
            strike_sync_all()
        elif key == "fidelity":
            from .fidelity import sync as fidelity_sync
            result = fidelity_sync.sync_all()
            return ("ok", result.get("method") or "")
        elif key == "onchain":
            from .onchain import sync as onchain_sync
            result = onchain_sync.sync_all()
            errs = result.get("errors") or []
            if errs:
                return ("error", "; ".join(errs))
        return ("ok", "")
    except Exception as exc:
        return ("error", str(exc))


def _job() -> None:
    for key in [s["key"] for s in get_status()["sources"]]:
        _set_source(key, state="running")
        state, detail = _run_one(key)
        _set_source(key, state=state, detail=detail)
    try:  # record today's history point for the Trending view
        from .history import record_snapshot
        record_snapshot()
    except Exception:
        pass
    with _lock:
        _state["running"] = False
        _state["done_at"] = dt.datetime.utcnow().isoformat()


def start_refresh() -> bool:
    """Begin a background refresh. Returns False if one is already running."""
    global _thread
    with _lock:
        if _state["running"]:
            return False
        srcs = configured_sources()
        _state["running"] = True
        _state["started_at"] = dt.datetime.utcnow().isoformat()
        _state["done_at"] = None
        _state["sources"] = [
            {"key": k, "label": label, "state": "pending", "detail": ""} for k, label in srcs
        ]
    _thread = threading.Thread(target=_job, daemon=True, name="riskmon-refresh")
    _thread.start()
    return True
