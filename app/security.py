import base64
import json
import secrets as py_secrets

from argon2.low_level import Type, hash_secret_raw
from cryptography.fernet import Fernet

ARGON2_TIME_COST = 3
ARGON2_MEMORY_COST = 65536  # 64 MiB
ARGON2_PARALLELISM = 4
KEY_LEN = 32
SALT_LEN = 16


def derive_key(password: str, salt: bytes) -> bytes:
    raw = hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=ARGON2_TIME_COST,
        memory_cost=ARGON2_MEMORY_COST,
        parallelism=ARGON2_PARALLELISM,
        hash_len=KEY_LEN,
        type=Type.ID,
    )
    return base64.urlsafe_b64encode(raw)


def encrypt_blob(key: bytes, payload: dict) -> str:
    return Fernet(key).encrypt(json.dumps(payload).encode("utf-8")).decode("utf-8")


def decrypt_blob(key: bytes, token: str) -> dict:
    return json.loads(Fernet(key).decrypt(token.encode("utf-8")))


def new_salt() -> bytes:
    return py_secrets.token_bytes(SALT_LEN)
