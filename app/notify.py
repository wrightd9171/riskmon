"""Email notifications via SMTP. Standard library only."""
import smtplib
import ssl
from email.message import EmailMessage

REQUIRED = ("smtp_host", "smtp_port", "smtp_user", "smtp_password", "email_to")


def email_configured(cfg: dict) -> bool:
    return all(cfg.get(k) for k in REQUIRED)


def send_email(cfg: dict, subject: str, body: str, html: str | None = None) -> None:
    """Send an email. Raises RuntimeError with a helpful hint on auth failure."""
    host = cfg["smtp_host"].strip()
    port = int(cfg.get("smtp_port") or 465)
    user = cfg["smtp_user"].strip()
    password = cfg["smtp_password"]
    sender = (cfg.get("email_from") or user).strip()
    to = cfg["email_to"].strip()

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")

    ctx = ssl.create_default_context()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as s:
                s.login(user, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.ehlo()
                s.starttls(context=ctx)
                s.login(user, password)
                s.send_message(msg)
    except (smtplib.SMTPAuthenticationError, smtplib.SMTPServerDisconnected) as e:
        hint = ""
        if "yahoo" in host.lower():
            hint = " Yahoo requires a 16-character app password (not your login password)."
        elif "gmail" in host.lower() or "google" in host.lower():
            hint = " Gmail requires a 16-character app password (not your login password)."
        raise RuntimeError(
            f"Login rejected by {host}.{hint} [{type(e).__name__}]"
        ) from e
