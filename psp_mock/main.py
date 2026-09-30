"""Мок платёжного провайдера (PSP). Ведёт себя как настоящий, но без реальных денег:

- создаёт платёж и отдаёт ссылку на страницу оплаты;
- на странице оплаты можно нажать «Оплатить» или «Отклонить»;
- присылает магазину подписанный вебхук (HMAC-SHA256);
- повторяет вебхук, если магазин не ответил 200 (at-least-once);
- с заданной вероятностью присылает дубль вебхука — чтобы проверить идемпотентность;
- делает возвраты и тоже сообщает о них вебхуком.

Хранит всё в памяти: после перезапуска платежи пропадают. Для мока это нормально.
Swagger: http://localhost:8001/docs
"""

import hashlib
import hmac
import json
import logging
import os
import random
import threading
import time
import uuid
from datetime import UTC, datetime

import httpx
from fastapi import FastAPI, Form, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

WEBHOOK_URL = os.getenv("PSP_WEBHOOK_URL", "http://localhost:8000/api/v1/webhooks/psp")
WEBHOOK_SECRET = os.getenv("PSP_WEBHOOK_SECRET", "psp-secret-change-me")
PUBLIC_URL = os.getenv("PSP_PUBLIC_URL", "http://localhost:8001")
DUPLICATE_PROBABILITY = float(os.getenv("PSP_DUPLICATE_WEBHOOK_PROBABILITY", "0.3"))
WEBHOOK_DELAY_SECONDS = float(os.getenv("PSP_WEBHOOK_DELAY_SECONDS", "1"))
CREATE_FAILURE_PROBABILITY = float(os.getenv("PSP_CREATE_FAILURE_PROBABILITY", "0"))
MAX_WEBHOOK_ATTEMPTS = 5

logging.basicConfig(level=logging.INFO, format="%(asctime)s psp-mock %(levelname)s %(message)s")
log = logging.getLogger("psp")

app = FastAPI(title="PSP Mock", description="Фейковый платёжный провайдер для ShopCore")

_lock = threading.Lock()
payments: dict[str, dict] = {}
refunds: dict[str, dict] = {}
idempotency: dict[str, str] = {}  # Idempotency-Key -> id созданного объекта


class PaymentCreate(BaseModel):
    order_id: int
    amount_kopecks: int = Field(gt=0)


class RefundCreate(BaseModel):
    payment_id: str
    amount_kopecks: int = Field(gt=0)


# ---------- Вебхуки ----------


def sign(timestamp: str, body: bytes) -> str:
    return hmac.new(WEBHOOK_SECRET.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def _deliver(event: dict) -> None:
    """Доставка с повторами: 1, 2, 4, 8 секунд между попытками."""
    body = json.dumps(event).encode()
    for attempt in range(1, MAX_WEBHOOK_ATTEMPTS + 1):
        timestamp = str(int(time.time()))
        try:
            response = httpx.post(
                WEBHOOK_URL,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-PSP-Timestamp": timestamp,
                    "X-PSP-Signature": sign(timestamp, body),
                },
                timeout=5,
            )
            if response.status_code == 200:
                log.info("Вебхук %s доставлен (попытка %s): %s", event["type"], attempt, response.text)
                return
            log.warning("Магазин ответил %s на вебхук %s", response.status_code, event["type"])
        except httpx.HTTPError as exc:
            log.warning("Магазин недоступен (попытка %s): %s", attempt, exc)
        time.sleep(2 ** (attempt - 1))
    log.error("Вебхук %s не доставлен после %s попыток", event["type"], MAX_WEBHOOK_ATTEMPTS)


def send_webhook(event_type: str, obj: dict, *, force_duplicate: bool = False, delay: float | None = None) -> None:
    event = {
        "event_id": str(uuid.uuid4()),
        "type": event_type,
        "payment_id": obj.get("payment_id"),
        "refund_id": obj.get("refund_id"),
        "order_id": obj.get("order_id"),
        "amount_kopecks": obj["amount_kopecks"],
        "occurred_at": datetime.now(UTC).isoformat(),
    }

    def worker():
        time.sleep(WEBHOOK_DELAY_SECONDS if delay is None else delay)
        _deliver(event)
        # Дубль — то же самое уведомление ещё раз, как бывает у настоящих PSP
        if force_duplicate or random.random() < DUPLICATE_PROBABILITY:
            log.info("Отправляю дубль вебхука %s", event_type)
            time.sleep(0.5)
            _deliver(event)

    threading.Thread(target=worker, daemon=True).start()


# ---------- API для магазина ----------


@app.post("/v1/payments", status_code=201, summary="Создать платёж")
def create_payment(body: PaymentCreate, idempotency_key: str | None = Header(None)):
    with _lock:
        if idempotency_key and idempotency_key in idempotency:
            return payments[idempotency[idempotency_key]]
        if random.random() < CREATE_FAILURE_PROBABILITY:
            raise HTTPException(503, "PSP temporarily unavailable")
        payment_id = "pay_" + uuid.uuid4().hex[:16]
        payment = {
            "payment_id": payment_id,
            "order_id": body.order_id,
            "amount_kopecks": body.amount_kopecks,
            "status": "pending",
            "payment_url": f"{PUBLIC_URL}/pay/{payment_id}",
        }
        payments[payment_id] = payment
        if idempotency_key:
            idempotency[idempotency_key] = payment_id
    log.info("Создан платёж %s на %s коп. по заказу %s", payment_id, body.amount_kopecks, body.order_id)
    return payment


@app.get("/v1/payments/{payment_id}", summary="Статус платежа")
def get_payment(payment_id: str):
    if payment_id not in payments:
        raise HTTPException(404, "Payment not found")
    return payments[payment_id]


@app.post("/v1/refunds", status_code=201, summary="Создать возврат")
def create_refund(body: RefundCreate, idempotency_key: str | None = Header(None)):
    with _lock:
        if idempotency_key and idempotency_key in idempotency:
            return refunds[idempotency[idempotency_key]]
        payment = payments.get(body.payment_id)
        if payment is None:
            raise HTTPException(404, "Payment not found")
        if payment["status"] != "succeeded":
            raise HTTPException(409, "Payment is not succeeded")
        refund_id = "ref_" + uuid.uuid4().hex[:16]
        refund = {
            "refund_id": refund_id,
            "payment_id": body.payment_id,
            "order_id": payment["order_id"],
            "amount_kopecks": body.amount_kopecks,
            "status": "pending",
        }
        refunds[refund_id] = refund
        if idempotency_key:
            idempotency[idempotency_key] = refund_id
    # Возврат подтверждаем чуть позже: магазин должен успеть сохранить у себя refund_id
    send_webhook("refund.succeeded", refund, delay=max(WEBHOOK_DELAY_SECONDS, 2))
    log.info("Создан возврат %s по платежу %s", refund_id, body.payment_id)
    return refund


# ---------- Страница оплаты для покупателя ----------

PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Оплата заказа</title>
<style>
body{{font-family:system-ui,sans-serif;background:#f4f5f7;display:flex;justify-content:center;padding:48px 16px;margin:0}}
.card{{background:#fff;border-radius:12px;padding:28px;max-width:380px;width:100%;box-shadow:0 2px 12px rgba(0,0,0,.08)}}
h1{{font-size:20px;margin:0 0 4px}} .muted{{color:#6b7280;font-size:14px}} .sum{{font-size:32px;font-weight:700;margin:20px 0}}
button{{width:100%;padding:12px;border:0;border-radius:8px;font-size:16px;cursor:pointer;margin-top:10px}}
.pay{{background:#16a34a;color:#fff}} .decline{{background:#e5e7eb}} .status{{margin-top:16px;font-weight:600}}
</style></head><body><div class="card">
<h1>Тестовый платёжный шлюз</h1><div class="muted">Заказ №{order_id} · платёж {payment_id}</div>
<div class="sum">{amount} ₽</div>{content}</div></body></html>"""


def _render(payment: dict, content: str) -> HTMLResponse:
    amount = f"{payment['amount_kopecks'] / 100:,.2f}".replace(",", " ").replace(".", ",")
    return HTMLResponse(
        PAGE.format(order_id=payment["order_id"], payment_id=payment["payment_id"], amount=amount, content=content)
    )


@app.get("/pay/{payment_id}", response_class=HTMLResponse, summary="Страница оплаты")
def payment_page(payment_id: str):
    payment = payments.get(payment_id)
    if payment is None:
        raise HTTPException(404, "Платёж не найден")
    if payment["status"] != "pending":
        return _render(payment, f'<div class="status">Статус: {payment["status"]}</div>')
    form = f"""<form method="post" action="/pay/{payment_id}">
      <button class="pay" name="result" value="success">Оплатить</button>
      <button class="decline" name="result" value="fail">Отклонить</button></form>"""
    return _render(payment, form)


@app.post("/pay/{payment_id}", summary="Покупатель оплатил или отклонил платёж")
def confirm_payment(payment_id: str, result: str = Form("success")):
    return _complete(payment_id, result)


@app.post(
    "/v1/payments/{payment_id}/complete",
    summary="То же без браузера: для Postman и тестов",
    description="result=success|fail. amount_override — подменить сумму в вебхуке (демо расхождения).",
)
def complete_api(payment_id: str, result: str = "success", amount_override: int | None = None, duplicate: bool = False):
    _complete(payment_id, result, amount_override=amount_override, force_duplicate=duplicate)
    return payments[payment_id]


def _complete(payment_id: str, result: str, amount_override: int | None = None, force_duplicate: bool = False):
    with _lock:
        payment = payments.get(payment_id)
        if payment is None:
            raise HTTPException(404, "Платёж не найден")
        if payment["status"] != "pending":
            return _render(payment, f'<div class="status">Уже обработан: {payment["status"]}</div>')
        payment["status"] = "succeeded" if result == "success" else "failed"
    event_obj = dict(payment)
    if amount_override is not None:
        event_obj["amount_kopecks"] = amount_override
    send_webhook(f"payment.{payment['status']}", event_obj, force_duplicate=force_duplicate)
    text = "Оплата прошла. Можно вернуться в магазин." if result == "success" else "Платёж отклонён."
    return _render(payment, f'<div class="status">{text}</div>')


@app.post("/v1/payments/{payment_id}/resend-webhook", summary="Повторить последний вебхук (демо дубля)")
def resend(payment_id: str):
    payment = payments.get(payment_id)
    if payment is None or payment["status"] == "pending":
        raise HTTPException(409, "Нет вебхука для повтора")
    send_webhook(f"payment.{payment['status']}", payment)
    return {"resent": True}
