"""Pushover push notifications. https://pushover.net/api"""
import httpx

API_URL = "https://api.pushover.net/1/messages.json"
REQUIRED = ("pushover_token", "pushover_user_key")

# Pushover hard limits.
TITLE_MAX = 250
MESSAGE_MAX = 1024


def pushover_configured(cfg: dict) -> bool:
    return all((cfg.get(k) or "").strip() for k in REQUIRED)


def send_pushover(
    cfg: dict,
    title: str,
    message: str,
    html: bool = False,
    url: str | None = None,
    url_title: str | None = None,
) -> None:
    """Send a Pushover push. Raises RuntimeError with a helpful hint on failure.

    Pushover supports a small HTML subset in the message when html=True:
    <b>, <i>, <u>, <font color="#rrggbb">, and <a href>. `url`/`url_title`
    render as a tappable supplementary link below the message.
    """
    token = (cfg.get("pushover_token") or "").strip()
    user = (cfg.get("pushover_user_key") or "").strip()
    if not token or not user:
        raise RuntimeError("Pushover is not configured (need API token and user key).")

    payload = {
        "token": token,
        "user": user,
        "title": title[:TITLE_MAX],
        "message": message[:MESSAGE_MAX],
    }
    if html:
        payload["html"] = "1"
    if url:
        payload["url"] = url[:512]
    if url_title:
        payload["url_title"] = url_title[:100]
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(API_URL, data=payload)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Could not reach Pushover: {exc}") from exc

    if resp.status_code == 200:
        return

    # Pushover returns JSON like {"errors": ["application token is invalid"]}.
    detail = ""
    try:
        body = resp.json()
        detail = "; ".join(body.get("errors", [])) or str(body.get("error", ""))
    except ValueError:
        detail = resp.text[:200]
    hint = ""
    if resp.status_code == 400 and "user" in detail.lower():
        hint = " Check the user key."
    elif resp.status_code == 400 and "token" in detail.lower():
        hint = " Check the API token."
    raise RuntimeError(f"Pushover rejected the request ({resp.status_code}): {detail}.{hint}")
