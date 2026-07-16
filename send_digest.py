"""Standalone portfolio-digest sender for Windows Task Scheduler.

Sends the portfolio summary via Pushover using credentials from the encrypted
store, independent of the running app. Run it from the project root with the
project's venv Python:

    .venv\\Scripts\\python.exe send_digest.py

The master password is read from, in order:
  1. the RISKMON_MASTER_PASSWORD environment variable, or
  2. the file named by RISKMON_MASTER_PASSWORD_FILE (contents = the password).

Do NOT put the password file inside the OneDrive-synced project tree.

Exit codes: 0 sent · 1 send failed · 2 config/setup error · 3 wrong password.
"""
import os
import sys


def _master_password() -> str:
    pw = os.environ.get("RISKMON_MASTER_PASSWORD")
    if pw:
        return pw
    path = os.environ.get("RISKMON_MASTER_PASSWORD_FILE")
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError as exc:
            print(f"Cannot read RISKMON_MASTER_PASSWORD_FILE: {exc}", file=sys.stderr)
            sys.exit(2)
    print(
        "No master password provided. Set RISKMON_MASTER_PASSWORD or "
        "RISKMON_MASTER_PASSWORD_FILE.",
        file=sys.stderr,
    )
    sys.exit(2)


def main() -> int:
    # Import after arg handling so --help stays fast and import errors are clear.
    from app import scheduler
    from app.db import init_db
    from app.secrets_store import store

    if not store.is_initialized():
        print("Store is not initialized — run the app's first-run setup first.", file=sys.stderr)
        return 2
    if not store.unlock(_master_password()):
        print("Incorrect master password.", file=sys.stderr)
        return 3
    init_db()
    try:
        scheduler.send_digest_now()
    except Exception as exc:  # surface a one-line reason to the Task Scheduler log
        print(f"Send failed: {exc}", file=sys.stderr)
        return 1
    print("Portfolio digest sent via Pushover.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
