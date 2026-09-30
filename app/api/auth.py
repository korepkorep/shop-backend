from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user
from app.models import User
from app.schemas.auth import AccessToken, LoginRequest, RefreshRequest, RegisterRequest, TokenPair, UserOut
from app.services import auth as service

router = APIRouter(tags=["Аккаунт"])


@router.post("/auth/register", response_model=UserOut, status_code=201, summary="Регистрация (US-01)")
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    return service.register(db, body.email, body.password)


@router.post("/auth/login", response_model=TokenPair, summary="Вход: выдать access и refresh токены (US-02)")
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    return service.login(db, body.email, body.password, ip)


@router.post("/auth/refresh", response_model=AccessToken, summary="Новый access-токен по refresh-токену")
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    return service.refresh(db, body.refresh_token)


@router.get("/users/me", response_model=UserOut, summary="Профиль текущего пользователя")
def me(user: User = Depends(get_current_user)):
    return user
