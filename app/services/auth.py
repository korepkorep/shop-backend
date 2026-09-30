"""Регистрация и вход (US-01, US-02)."""

import threading
import time
from collections import defaultdict, deque

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models import User

INVALID_CREDENTIALS = AppError(401, "INVALID_CREDENTIALS", "Неверный email или пароль")


def register(db: Session, email: str, password: str) -> User:
    email = email.lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise AppError(409, "EMAIL_TAKEN", "Пользователь с таким email уже зарегистрирован")
    user = User(email=email, password_hash=hash_password(password), role="customer")
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:  # параллельная регистрация того же email
        db.rollback()
        raise AppError(409, "EMAIL_TAKEN", "Пользователь с таким email уже зарегистрирован") from exc
    return user


class LoginRateLimiter:
    """Не больше N неудачных попыток входа с одного IP за минуту (US-02 AC 5).

    Упрощение: счётчик в памяти процесса. В проде с несколькими экземплярами API
    счётчик хранят в Redis.
    """

    def __init__(self, limit: int, window_seconds: int = 60):
        self.limit = limit
        self.window = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, ip: str, now: float) -> deque[float]:
        attempts = self._failures[ip]
        while attempts and now - attempts[0] > self.window:
            attempts.popleft()
        return attempts

    def check(self, ip: str) -> None:
        with self._lock:
            if len(self._prune(ip, time.monotonic())) >= self.limit:
                raise AppError(429, "TOO_MANY_ATTEMPTS", "Слишком много попыток входа, попробуйте через минуту")

    def register_failure(self, ip: str) -> None:
        with self._lock:
            self._failures[ip].append(time.monotonic())

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()


login_limiter = LoginRateLimiter(settings.login_max_failures_per_minute)


def login(db: Session, email: str, password: str, ip: str) -> dict:
    login_limiter.check(ip)
    user = db.scalar(select(User).where(User.email == email.lower()))
    # Один и тот же ответ для «нет такого email» и «неверный пароль» (US-02 AC 2)
    if user is None or not verify_password(password, user.password_hash):
        login_limiter.register_failure(ip)
        raise INVALID_CREDENTIALS
    return {
        "access_token": create_access_token(user.id, user.role),
        "refresh_token": create_refresh_token(user.id, user.role),
    }


def refresh(db: Session, refresh_token: str) -> dict:
    payload = decode_token(refresh_token, "refresh")
    if payload is None:
        raise AppError(401, "UNAUTHORIZED", "Refresh-токен недействителен или истёк")
    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise AppError(401, "UNAUTHORIZED", "Пользователь не найден")
    return {"access_token": create_access_token(user.id, user.role)}
