"""Единый формат ошибок API: {"error": {"code", "message", "details"}}."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    """Бизнес-ошибка. Код (code) стабилен и описан в docs/04-api.md."""

    def __init__(self, status_code: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


# Частые ошибки — чтобы не повторять коды и статусы по всему коду
def not_found(what: str) -> AppError:
    return AppError(404, "NOT_FOUND", f"{what} не найден")


def invalid_transition(current: str, target: str) -> AppError:
    return AppError(
        409,
        "INVALID_TRANSITION",
        f"Нельзя перевести заказ из статуса {current} в {target}",
        {"current_status": current, "target_status": target},
    )


def _body(code: str, message: str, details: Any = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


_HTTP_CODES = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError):
        headers = {"Retry-After": "60"} if exc.status_code == 429 else None
        return JSONResponse(
            status_code=exc.status_code, content=_body(exc.code, exc.message, exc.details), headers=headers
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError):
        details = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]} for e in exc.errors()
        ]
        return JSONResponse(status_code=422, content=_body("VALIDATION_ERROR", "Некорректные данные запроса", details))

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(_: Request, exc: StarletteHTTPException):
        code = _HTTP_CODES.get(exc.status_code, "HTTP_ERROR")
        return JSONResponse(status_code=exc.status_code, content=_body(code, str(exc.detail)), headers=exc.headers)
