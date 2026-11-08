"""Пароли только как Argon2id. Открытый секрет в базу не пишется."""

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_HASHER = PasswordHasher()


def hash_password(password: str) -> str:
    if not isinstance(password, str) or not password.strip():
        raise ValueError("Пароль не задан")
    return _HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _HASHER.verify(password_hash, password)
    except VerifyMismatchError:
        return False
