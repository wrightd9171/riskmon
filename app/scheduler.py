"""On-demand digest sending via Pushover.

Scheduling lives OUTSIDE the app now (a Windows Task Scheduler job runs
send_digest.py on a cadence), so there is no in-process timer thread. The app
keeps send_digest_now() for the "Send test now" button and for the external
sender to call once unlocked.
"""
import datetime as dt

from .pushover import pushover_configured, send_pushover
from .report import build_pushover_summary
from .secrets_store import store


def _config_from_store() -> dict | None:
    if not store.is_unlocked():
        return None
    cfg = {
        "pushover_token": store.get("pushover_token") or "",
        "pushover_user_key": store.get("pushover_user_key") or "",
    }
    return cfg if pushover_configured(cfg) else None


def _missing_fields() -> list[str]:
    if not store.is_unlocked():
        return ["(store locked)"]
    missing = []
    for key, label in [("pushover_token", "API token"), ("pushover_user_key", "User key")]:
        if not (store.get(key) or "").strip():
            missing.append(label)
    return missing


def send_digest_now() -> None:
    """Build the portfolio summary and push it. Caller ensures the store is unlocked."""
    cfg = _config_from_store()
    if cfg is None:
        detail = ", ".join(_missing_fields()) or "unknown"
        raise RuntimeError(f"Pushover is not fully configured. Missing: {detail}")
    title, message = build_pushover_summary()
    send_pushover(cfg, title, message)
    store.update(notify_last_sent=dt.datetime.utcnow().isoformat())


def start() -> None:
    """No-op: scheduling is handled by an external Task Scheduler job."""
    return


def stop() -> None:
    return
