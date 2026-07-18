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
        # Best-effort auto-fill; an error here must NOT close the window.
        try:
            step_1, step_2 = browser.login(username=username, password=password, save_device=True)
            print(f"[auto-login: credentials_submitted={step_1}, logged_in_without_2FA={step_2}]")
            if step_2 is False:
                code = input(
                    "If Fidelity TEXTED you a code, type it and press Enter; "
                    "otherwise leave blank and approve the push in the window: "
                ).strip()
                if code:
                    try:
                        browser.login_2FA(code)
                    except Exception as exc:
                        print(f"[login_2FA note: {exc}]")
        except Exception as exc:
            print(f"[auto-login hit an error — just finish manually in the window: {exc}]")

        # Hold the window open until the user has actually logged in.
        input(
            "\nIn the Firefox window: finish logging in and approve the Duo 2FA. If you see a\n"
            "\"Don't ask again on this device\" / \"Remember this device\" option, CHECK it.\n"
            "When you can see your Fidelity accounts, press Enter here to save the session... "
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
