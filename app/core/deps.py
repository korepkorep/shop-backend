"""Зависимости FastAPI: текущий пользователь и проверка роли."""

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError
from app.core.security import decode_token
from app.models import User

# В Swagger появится кнопка Authorize: туда вставляется access-токен из /auth/login
bearer = HTTPBearer(auto_error=False, description="Access-токен из POST /api/v1/auth/login")


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)
) -> User:
    if creds is None:
        raise AppError(401, "UNAUTHORIZED", "Нужна авторизация: заголовок Authorization: Bearer <token>")
    payload = decode_token(creds.credentials, "access")
    if payload is None:
        raise AppError(401, "UNAUTHORIZED", "Токен недействителен или истёк")
    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise AppError(401, "UNAUTHORIZED", "Пользователь не найден")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise AppError(403, "FORBIDDEN", "Доступно только администратору")
    return user
