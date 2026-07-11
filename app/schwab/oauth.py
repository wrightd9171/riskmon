import datetime as dt
import http.server
import ipaddress
import ssl
import threading
import urllib.parse as urlparse
from typing import Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from ..config import (
    CALLBACK_HOST,
    CALLBACK_PORT,
    CALLBACK_URL,
    CERT_PATH,
    KEY_PATH,
    SCHWAB_AUTH_URL,
)
from ..secrets_store import store
from .client import exchange_code, save_tokens

_server: Optional[http.server.HTTPServer] = None
_server_thread: Optional[threading.Thread] = None
_lock = threading.Lock()
_result: dict = {"status": "idle", "error": None}


def build_authorize_url() -> str:
    client_id = store.get("client_id")
    if not client_id:
        raise RuntimeError("client_id not configured")
    params = {
        "client_id": client_id,
        "redirect_uri": CALLBACK_URL,
        "response_type": "code",
    }
    return f"{SCHWAB_AUTH_URL}?{urlparse.urlencode(params)}"


def status() -> dict:
    return dict(_result)


def _ensure_cert() -> None:
    if CERT_PATH.exists() and KEY_PATH.exists():
        return
    CERT_PATH.parent.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, CALLBACK_HOST),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(dt.datetime.utcnow() - dt.timedelta(days=1))
        .not_valid_after(dt.datetime.utcnow() + dt.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.ip_address(CALLBACK_HOST)),
            ]),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    KEY_PATH.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    CERT_PATH.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_a, **_kw):
        return

    def do_GET(self):
        global _result
        parsed = urlparse.urlparse(self.path)
        params = urlparse.parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        error = params.get("error", [None])[0]

        if error:
            _result = {"status": "error", "error": f"Schwab returned error: {error}"}
            self._respond("Authorization failed. You can close this tab.", error=True)
            return
        if not code:
            _result = {"status": "error", "error": "No authorization code in callback"}
            self._respond("No authorization code received. Close this tab.", error=True)
            return

        try:
            tokens = exchange_code(code)
            save_tokens(tokens)
        except Exception as exc:
            _result = {"status": "error", "error": f"Token exchange failed: {exc}"}
            self._respond("Token exchange failed. Close this tab and try again.", error=True)
            return

        _result = {"status": "success", "error": None}
        self._respond("Schwab connected. Close this tab and return to the Risk Monitor app.")
        threading.Thread(target=_shutdown_server, daemon=True).start()

    def _respond(self, message: str, error: bool = False) -> None:
        self.send_response(400 if error else 200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        color = "#b91c1c" if error else "#15803d"
        body = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Risk Monitor</title></head>
<body style="font-family: system-ui, sans-serif; padding: 48px; color: {color};">
<h2>{message}</h2>
</body></html>"""
        self.wfile.write(body.encode("utf-8"))


def _shutdown_server() -> None:
    global _server
    with _lock:
        srv = _server
        _server = None
    if srv is not None:
        try:
            srv.shutdown()
            srv.server_close()
        except Exception:
            pass


def start_callback_server() -> None:
    global _server, _server_thread, _result
    _ensure_cert()
    with _lock:
        if _server is not None:
            return
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=str(CERT_PATH), keyfile=str(KEY_PATH))
        try:
            _server = http.server.HTTPServer((CALLBACK_HOST, CALLBACK_PORT), _CallbackHandler)
        except OSError as exc:
            _result = {"status": "error", "error": f"Cannot bind {CALLBACK_HOST}:{CALLBACK_PORT}: {exc}"}
            raise
        _server.socket = ctx.wrap_socket(_server.socket, server_side=True)
        _result = {"status": "waiting", "error": None}
        _server_thread = threading.Thread(
            target=_server.serve_forever, daemon=True, name="schwab-oauth-callback"
        )
        _server_thread.start()
