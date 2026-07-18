"""One-time interactive Fidelity login for the Risk Monitor.

Opens a real (non-headless) browser window so you can clear Fidelity's 2FA once
— approve the Duo push on your phone, or type the texted code — then saves the
browser session to data/Fidelity_riskmon.json with the device remembered. After
that, the app's headless refresh reuses that session and skips 2FA, until
Fidelity expires it (just re-run this when refreshes start falling back to CSV).

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
        print(f"fidelity-api not installed ({exc}). Run: pip install fidelity-api && playwright install",
              file=sys.stderr)
        return 2

    print("Opening a browser window — credentials are filled automatically.")
    print("Complete 2FA in the window (approve the Duo push, or note the code Fidelity texts you).")
    browser = fidelity_lib.FidelityAutomation(
        headless=False, save_state=True,
        profile_path=str(DATA_DIR), title=SESSION_TITLE,
    )
    try:
        step_1, step_2 = browser.login(username=username, password=password, save_device=True)
        if not step_1:
            print("Could not submit credentials — check the username/password.", file=sys.stderr)
            return 1
        if not step_2:
            code = input(
                "Enter the code Fidelity texted you "
                "(leave blank only if you already finished 2FA in the browser window): "
            ).strip()
            if code:
                browser.login_2FA(code)
        info = browser.getAccountInfo()
        if info:
            print(f"Success. Fidelity accounts found: {', '.join(str(k) for k in info.keys())}")
        else:
            print("Logged in, but no accounts were returned. The session was still saved.",
                  file=sys.stderr)
    except Exception as exc:
        print(f"Interactive login failed: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            browser.close_browser()  # persists the session file
        except Exception:
            pass

    print(f"Session saved to {DATA_DIR}\\Fidelity_{SESSION_TITLE}.json — refreshes will reuse it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
