from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = APP_ROOT / "data"
SECRETS_PATH = DATA_DIR / "secrets.enc"
DB_PATH = DATA_DIR / "portfolio.db"
CERT_PATH = DATA_DIR / "cert.pem"
KEY_PATH = DATA_DIR / "key.pem"

APP_HOST = "127.0.0.1"
APP_PORT = 8000

CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 8182
CALLBACK_URL = f"https://{CALLBACK_HOST}:{CALLBACK_PORT}"

SCHWAB_AUTH_URL = "https://api.schwabapi.com/v1/oauth/authorize"
SCHWAB_TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
SCHWAB_API_BASE = "https://api.schwabapi.com/trader/v1"
