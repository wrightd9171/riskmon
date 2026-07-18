"""One-time interactive Fidelity login for the Risk Monitor.

Opens a real Firefox window so you can complete Fidelity's login + 2FA by hand
(approve the Duo push, check any "remember this device" box). It then saves the
browser session to data/Fidelity_riskmon.json, which the app's headless refresh
reuses so it can skip 2FA — until Fidelity expires it (re-run this then).

Run from the project root:
    .venv\\Scripts\\python.exe fidelity_login.py
"""
import getpass
import sys


def main() -> int:
    from app.config import DATA_DIR
    from app.fidelity.api_sync import SESSION_TITLE
    from app.secrets_store import store

    if not store.is_initialized():
        print("Store is not initialized — run the app's first-run setup first.", file=sys.stderr)
        return 2
    if not store.unlock(getpass.getpass("Master password: ")):
        print("Incorrect master password.", file=sys.stderr)
        return 3

    username = (store.get("fidelity_username") or "").strip()
    password = (store.get("fidelity_password") or "").strip()
    totp_secret = (store.get("fidelity_totp_secret") or "").strip()
    if not username or not password:
        print("No Fidelity username/password saved — enter them on Settings -> Fidelity first.",
              file=sys.stderr)
        return 2

    try:
        from fidelity import fidelity as fidelity_lib
    except Exception as exc:
        print(f"fidelity-api not installed ({exc}). "
              "Run: pip install fidelity-api && playwright install firefox", file=sys.stderr)
        return 2

    print("Opening a Firefox window. It will TRY to fill your login automatically,")
    print("but you may need to finish by hand. Do NOT close the window yourself —")
    print("come back to THIS console and press Enter when you are done.\n")

    browser = fidelity_lib.FidelityAutomation(
        headless=False, save_state=True,
        profile_path=str(DATA_DIR), title=SESSION_TITLE,
    )
    try:
        step_1 = step_2 = None
        try:
            step_1, step_2 = browser.login(
                username=username, password=password,
                totp_secret=(totp_secret or None), save_device=True,
            )
            print(f"[auto-login: credentials_submitted={step_1}, logged_in_without_2FA={step_2}]")
        except Exception as exc:
            print(f"[auto-login error: {exc}]")

        if step_2 is True:
            print("Logged in automatically.")
        elif not totp_secret:
            print(
                "\nFidelity is challenging with an AUTHENTICATOR-APP code (TOTP) — the kind\n"
                "Duo Mobile generates as a 6-digit passcode. This tool can only auto-enter it\n"
                "if you give it the secret SEED. Best options:\n"
                "  1) Add the TOTP secret on Settings -> Fidelity (README 'Fidelity') — then it\n"
                "     works automatically, including the daily refresh. RECOMMENDED.\n"
                "  2) Keep using the Fidelity CSV export — it works and includes cost basis.\n"
                "(Finishing by hand in the window is hit-or-miss for this screen.)\n"
            )

        input(
            "\nTo try finishing manually anyway: complete login/2FA in the Firefox window\n"
            "(check any 'remember this device' box), then press Enter here to save. Otherwise\n"
            "just press Enter to exit... "
        )

        try:
            info = browser.getAccountInfo()
            if info:
                print(f"Saved. Fidelity accounts found: {', '.join(str(k) for k in info.keys())}")
            else:
                print("No accounts read, but the session was saved — a Refresh will show if it worked.")
        except Exception as exc:
            print(f"[getAccountInfo note: {exc}] — session still saved.")
    finally:
        try:
            browser.close_browser()  # persists data/Fidelity_riskmon.json
        except Exception as exc:
            print(f"[close/save note: {exc}]")

    print(f"\nSession file: {DATA_DIR}\\Fidelity_{SESSION_TITLE}.json")
    print("Now click Refresh in the app — Fidelity should sync via the API (falls back to CSV if not).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
