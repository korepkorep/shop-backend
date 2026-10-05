"""Схема ошибки для документации OpenAPI. Сам формат собирают обработчики в app/core/errors.py."""

from typing import Any

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str = Field(
        description="Стабильный машинный код ошибки, по нему клиент выбирает поведение", examples=["OUT_OF_STOCK"]
    )
    message: str = Field(
        description="Сообщение для человека, может меняться", examples=["Недостаточно товара на складе"]
    )
    details: Any = Field(default=None, description="Подробности: поля с ошибками, остатки и т.п. Может быть null")


class ErrorResponse(BaseModel):
    """Единый формат всех ошибок API (docs/04-api.md, раздел 3)."""

    error: ErrorBody


_DESCRIPTIONS = {
    401: "Нет токена, токен истёк или подделан (UNAUTHORIZED, INVALID_CREDENTIALS)",
    403: "Недостаточно прав (FORBIDDEN)",
    404: "Объект не найден (NOT_FOUND)",
    409: "Конфликт с текущим состоянием данных",
    422: "Некорректные данные запроса (VALIDATION_ERROR) или нарушен лимит",
    429: "Слишком много попыток (TOO_MANY_ATTEMPTS), см. заголовок Retry-After",
}


def error_responses(*status_codes: int, **overrides: str) -> dict:
    """Описание ошибок для параметра responses= у эндпоинта.

    Пример: error_responses(401, 404, s409="PRODUCT_INACTIVE, OUT_OF_STOCK")
    """
    result = {}
    for status in status_codes:
        description = overrides.get(f"s{status}", _DESCRIPTIONS[status])
        result[status] = {"model": ErrorResponse, "description": description}
    return result
