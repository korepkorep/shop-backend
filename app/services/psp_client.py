"""HTTP-клиент платёжного провайдера (PSP). Контракт описан в docs/06-integrations.md."""

import logging

import httpx

from app.core.config import settings
from app.core.errors import AppError

log = logging.getLogger(__name__)


class PSPError(Exception):
    pass


def _post(path: str, json: dict, idempotency_key: str) -> dict:
    try:
        response = httpx.post(
            f"{settings.psp_base_url}{path}",
            json=json,
            headers={"Idempotency-Key": idempotency_key},
            timeout=settings.psp_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        log.warning("PSP недоступен: %s", exc)
        raise PSPError(str(exc)) from exc
    if response.status_code >= 400:
        log.warning("PSP вернул ошибку %s: %s", response.status_code, response.text)
        raise PSPError(f"PSP HTTP {response.status_code}")
    return response.json()


def create_payment(order_id: int, amount_kopecks: int, idempotency_key: str) -> dict:
    """Возвращает {"payment_id", "payment_url", "status"}."""
    try:
        return _post("/v1/payments", {"order_id": order_id, "amount_kopecks": amount_kopecks}, idempotency_key)
    except PSPError as exc:
        raise AppError(503, "PSP_UNAVAILABLE", "Платёжный провайдер недоступен, попробуйте позже") from exc


def create_refund(provider_payment_id: str, amount_kopecks: int, idempotency_key: str) -> dict:
    """Возвращает {"refund_id", "status"}. Бросает PSPError — вызывающий решает, что делать."""
    return _post("/v1/refunds", {"payment_id": provider_payment_id, "amount_kopecks": amount_kopecks}, idempotency_key)
