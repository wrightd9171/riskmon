"""Background thread that sends the weekly digest at the configured time."""
import datetime as dt
import threading
import time
import traceback

from .notify import email_configured, send_email
from .report import build_digest
from .secrets_store import store

CHECK_INTERVAL_SECS = 60
MIN_SEND_INTERVAL_SECS = 6 * 24 * 3600  # never send more than once every 6 days
_thread: threading.Thread | None = None
_stop = threading.Event()


def _config_from_store(require_enabled: bool = True) -> dict | None:
    if not store.is_unlocked():
        return None
    if require_enabled and not store.get("notify_enabled"):
        return None
    cfg = {
        "smtp_host": store.get("notify_smtp_host") or "",
        "smtp_port": store.get("notify_smtp_port") or 465,
        "smtp_user": store.get("notify_smtp_user") or "",
        "smtp_password": store.get("notify_smtp_password") or "",
        "email_from": store.get("notify_email_from") or "",
        "email_to": store.get("notify_email_to") or "",
    }
    if not email_configured(cfg):
        return None
    return cfg


def _missing_fields() -> list[str]:
    if not store.is_unlocked():
        return ["(store locked)"]
    missing = []
    for k, label in [
        ("notify_smtp_host", "SMTP host"),
        ("notify_smtp_port", "SMTP port"),
        ("notify_smtp_user", "SMTP user"),
        ("notify_smtp_password", "App password"),
        ("notify_email_to", "Send to"),
    ]:
        if not store.get(k):
            missing.append(label)
    return missing


def _is_scheduled_now(now: dt.datetime) -> bool:
    target_dow = int(store.get("notify_dow", 6))  # default Sunday
    target_hour = int(store.get("notify_hour", 8))  # default 8am
    target_minute = int(store.get("notify_minute", 0))
    if now.weekday() != target_dow:
        return False
    if now.hour != target_hour:
        return False
    # Fire within the first minute of the target hour:minute
    return now.minute == target_minute


def _sent_recently() -> bool:
    last = store.get("notify_last_sent")
    if not last:
        return False
    try:
        prev = dt.datetime.fromisoformat(last)
    except ValueError:
        return False
    return (dt.datetime.utcnow() - prev).total_seconds() < MIN_SEND_INTERVAL_SECS


def send_digest_now() -> None:
    """Build the digest and send it. Callers should ensure the store is unlocked."""
    cfg = _config_from_store(require_enabled=False)
    if cfg is None:
        missing = _missing_fields()
        detail = ", ".join(missing) if missing else "unknown"
        raise RuntimeError(f"Email is not fully configured. Missing: {detail}")
    subject, plain, html = build_digest()
    send_email(cfg, subject, plain, html=html)
    store.update(notify_last_sent=dt.datetime.utcnow().isoformat())


def _loop() -> None:
    while not _stop.is_set():
        try:
            cfg = _config_from_store(require_enabled=True)
            if cfg is not None:
                now = dt.datetime.now()
                if _is_scheduled_now(now) and not _sent_recently():
                    subject, plain, html = build_digest()
                    send_email(cfg, subject, plain, html=html)
                    store.update(notify_last_sent=dt.datetime.utcnow().isoformat())
        except Exception:
            traceback.print_exc()
        _stop.wait(CHECK_INTERVAL_SECS)


def start() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="risk-monitor-scheduler")
    _thread.start()


def stop() -> None:
    _stop.set()
