"""Пароли Argon2id, токены только в виде хеша, ограничение попыток входа."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_HASHER = PasswordHasher()


def hash_password(password: str) -> str:
    if not password.strip():
        raise ValueError("Пароль не задан")
    return _HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _HASHER.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class LoginLimiter:
    def __init__(self, *, limit: int = 5, lock_minutes: int = 15) -> None:
        self.limit = limit
        self.lock_minutes = lock_minutes
        self._failures: dict[tuple[str, str], tuple[int, datetime | None]] = {}

    def blocked(self, username: str, address: str, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        count, until = self._failures.get((username, address), (0, None))
        return until is not None and current < until and count >= self.limit

    def fail(self, username: str, address: str, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        count, _until = self._failures.get((username, address), (0, None))
        count += 1
        until = current + timedelta(minutes=self.lock_minutes) if count >= self.limit else None
        self._failures[(username, address)] = (count, until)

    def reset(self, username: str, address: str) -> None:
        self._failures.pop((username, address), None)
