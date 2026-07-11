import base64
import json
import threading
from pathlib import Path
from typing import Any, Optional

from cryptography.fernet import InvalidToken

from .config import SECRETS_PATH
from .security import decrypt_blob, derive_key, encrypt_blob, new_salt


class SecretsStore:
    """In-memory holder of decrypted secrets, backed by an encrypted file on disk."""

    def __init__(self, path: Path = SECRETS_PATH):
        self.path = path
        self._lock = threading.Lock()
        self._key: Optional[bytes] = None
        self._salt: Optional[bytes] = None
        self._data: Optional[dict] = None

    def is_initialized(self) -> bool:
        return self.path.exists()

    def is_unlocked(self) -> bool:
        return self._data is not None

    def initialize(self, password: str, payload: dict) -> None:
        salt = new_salt()
        key = derive_key(password, salt)
        with self._lock:
            self._salt = salt
            self._key = key
            self._data = dict(payload)
            self._write_locked()

    def unlock(self, password: str) -> bool:
        if not self.path.exists():
            return False
        raw = json.loads(self.path.read_text())
        salt = base64.b64decode(raw["salt"])
        key = derive_key(password, salt)
        try:
            data = decrypt_blob(key, raw["ciphertext"])
        except InvalidToken:
            return False
        with self._lock:
            self._salt = salt
            self._key = key
            self._data = data
        return True

    def lock(self) -> None:
        with self._lock:
            self._key = None
            self._salt = None
            self._data = None

    def get(self, name: str, default: Any = None) -> Any:
        if self._data is None:
            raise RuntimeError("SecretsStore is locked")
        return self._data.get(name, default)

    def update(self, **fields: Any) -> None:
        if self._data is None:
            raise RuntimeError("SecretsStore is locked")
        with self._lock:
            self._data.update(fields)
            self._write_locked()

    def change_password(self, new_password: str) -> None:
        if self._data is None:
            raise RuntimeError("SecretsStore is locked")
        with self._lock:
            self._salt = new_salt()
            self._key = derive_key(new_password, self._salt)
            self._write_locked()

    def _write_locked(self) -> None:
        assert self._key is not None and self._salt is not None and self._data is not None
        payload = {
            "salt": base64.b64encode(self._salt).decode("ascii"),
            "ciphertext": encrypt_blob(self._key, self._data),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".enc.tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(self.path)


store = SecretsStore()
