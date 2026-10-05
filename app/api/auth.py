from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user
from app.models import User
from app.schemas.auth import AccessToken, LoginRequest, RefreshRequest, RegisterRequest, TokenPair, UserOut
from app.schemas.errors import error_responses
from app.services import auth as service

router = APIRouter(tags=["Аккаунт"])


@router.post(
    "/auth/register",
    response_model=UserOut,
    status_code=201,
    summary="Регистрация (US-01)",
    responses=error_responses(409, 422, s409="EMAIL_TAKEN — email уже зарегистрирован"),
)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    return service.register(db, body.email, body.password)


@router.post(
    "/auth/login",
    response_model=TokenPair,
    summary="Вход: выдать access и refresh токены (US-02)",
    responses=error_responses(401, 422, 429, s401="INVALID_CREDENTIALS — неверный email или пароль"),
)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    return service.login(db, body.email, body.password, ip)


@router.post(
    "/auth/refresh",
    response_model=AccessToken,
    summary="Новый access-токен по refresh-токену",
    responses=error_responses(401, 422),
)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    return service.refresh(db, body.refresh_token)


@router.get(
    "/users/me", response_model=UserOut, summary="Профиль текущего пользователя", responses=error_responses(401)
)
def me(user: User = Depends(get_current_user)):
    return user
