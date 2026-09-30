"""Оплата и возвраты: создание платежа, обработка вебхуков PSP (US-10, US-11, US-12, US-18)."""

import hashlib
import hmac
import logging
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError, invalid_transition, not_found
from app.models import Incident, Order, Payment, Refund, User
from app.services import messaging, psp_client
from app.services.orders import get_user_order, lock_stock
from app.services.statuses import transition

log = logging.getLogger(__name__)

FINAL_PAYMENT_STATUSES = {"succeeded", "failed", "amount_mismatch", "refund_pending", "refunded"}


def serialize_payment(p: Payment) -> dict:
    return {
        "id": p.id,
        "order_id": p.order_id,
        "provider_payment_id": p.provider_payment_id,
        "amount_kopecks": p.amount_kopecks,
        "status": p.status,
        "payment_url": p.payment_url,
    }


def incident(db: Session, kind: str, order_id: int | None, **details) -> None:
    db.add(Incident(kind=kind, order_id=order_id, details=details))
    log.warning("Инцидент %s", kind, extra={"order_id": order_id})


# ---------- Создание платежа ----------


def create_payment(db: Session, user: User, order_id: int, client_key: str) -> tuple[int, dict]:
    """Коммит делает run_idempotent. Внимание: вызов PSP идёт внутри транзакции,
    строка заказа заблокирована до ответа PSP (до 5 секунд) — осознанное упрощение."""
    order = get_user_order(db, user, order_id, for_update=True)
    if order.status != "created":
        raise invalid_transition(order.status, "paid")
    if order.expires_at <= datetime.now(UTC):
        raise AppError(409, "ORDER_EXPIRED", "Срок оплаты заказа истёк")

    # BR-06: если активный платёж уже есть — отдаём его, новый не создаём
    pending = db.scalar(select(Payment).where(Payment.order_id == order.id, Payment.status == "pending"))
    if pending:
        return 200, serialize_payment(pending)

    # Ключ идемпотентности для PSP: повтор нашего запроса с тем же ключом не создаст второй платёж в PSP
    psp = psp_client.create_payment(order.id, order.total_kopecks, idempotency_key=f"{user.id}:{client_key}")
    payment = Payment(
        order_id=order.id,
        provider_payment_id=psp["payment_id"],
        amount_kopecks=order.total_kopecks,
        status="pending",
        payment_url=psp["payment_url"],
    )
    db.add(payment)
    db.flush()
    return 201, serialize_payment(payment)


# ---------- Вебхуки PSP ----------


def verify_signature(raw_body: bytes, timestamp: str | None, signature: str | None) -> None:
    """HMAC-SHA256 от "<timestamp>.<тело>" общим секретом + проверка свежести (US-11 AC 1-2)."""
    if not timestamp or not signature:
        raise AppError(401, "INVALID_SIGNATURE", "Нет подписи вебхука")
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise AppError(401, "INVALID_SIGNATURE", "Некорректная временная метка") from exc
    if abs(time.time() - ts) > settings.webhook_max_age_seconds:
        raise AppError(401, "INVALID_SIGNATURE", "Вебхук устарел")
    expected = hmac.new(
        settings.psp_webhook_secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256
    ).hexdigest()
    # compare_digest сравнивает за постоянное время — защита от timing-атак
    if not hmac.compare_digest(expected, signature):
        raise AppError(401, "INVALID_SIGNATURE", "Неверная подпись вебхука")


def handle_webhook(db: Session, event: dict) -> dict:
    event_type = event.get("type", "")
    if event_type.startswith("payment."):
        result = _handle_payment_event(db, event)
    elif event_type.startswith("refund."):
        result = _handle_refund_event(db, event)
    else:
        incident(db, "unknown_webhook_type", None, event=event)
        result = "ignored"
    db.commit()
    return {"result": result}


def _handle_payment_event(db: Session, event: dict) -> str:
    payment = db.scalar(
        select(Payment)
        .where(Payment.provider_payment_id == event.get("payment_id"))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if payment is None:
        incident(db, "unknown_payment", None, event=event)
        return "unknown_payment"  # 200, чтобы PSP не повторял бесконечно (US-11 AC 7)

    if payment.status in FINAL_PAYMENT_STATUSES:
        log.info("Дубль вебхука, ничего не меняем", extra={"order_id": payment.order_id})
        return "duplicate"  # US-11 AC 4

    if event.get("amount_kopecks") != payment.amount_kopecks:
        payment.status = "amount_mismatch"
        incident(
            db,
            "amount_mismatch",
            payment.order_id,
            expected=payment.amount_kopecks,
            received=event.get("amount_kopecks"),
            provider_payment_id=payment.provider_payment_id,
        )
        return "amount_mismatch"

    if event["type"] == "payment.failed":
        payment.status = "failed"  # заказ остаётся created — можно попробовать снова (US-11 AC 5)
        return "failed"

    if event["type"] != "payment.succeeded":
        incident(db, "unknown_webhook_type", payment.order_id, event=event)
        return "ignored"

    payment.status = "succeeded"
    order = db.scalar(
        select(Order).where(Order.id == payment.order_id).with_for_update().execution_options(populate_existing=True)
    )
    user = db.get(User, order.user_id)

    if order.status == "created":
        _mark_paid(db, order, user, reason="payment_succeeded", from_reserve=True)
        return "paid"

    if order.status == "expired":
        # BR-10: поздняя оплата. Хватает товара — восстанавливаем заказ, иначе возврат
        stock = lock_stock(db, [i.product_id for i in order.items])
        if all(stock[i.product_id].available >= i.quantity for i in order.items):
            _mark_paid(db, order, user, reason="late_payment", from_reserve=False)
            incident(db, "late_payment_restored", order.id)
            return "paid_late"
        _request_refund(db, payment, reason="late_payment")
        incident(db, "late_payment_refunded", order.id)
        return "refund_requested"

    # Оплата отменённого заказа (или второго платежа) — всегда возврат (US-12 AC 2)
    reason = "cancelled_order" if order.status == "cancelled" else "duplicate_payment"
    _request_refund(db, payment, reason=reason)
    incident(db, f"payment_for_{order.status}_order", order.id)
    return "refund_requested"


def _mark_paid(db: Session, order: Order, user: User, *, reason: str, from_reserve: bool) -> None:
    transition(db, order, "paid", actor="psp", reason=reason)
    stock = lock_stock(db, [i.product_id for i in order.items])
    for item in order.items:
        stock[item.product_id].quantity -= item.quantity  # товар уходит со склада
        if from_reserve:
            stock[item.product_id].reserved -= item.quantity  # и из резерва
    messaging.publish_order_event(db, order, "OrderPaid", {"late": reason == "late_payment"})
    messaging.send_notification(db, user, "receipt", order)
    log.info("Заказ оплачен", extra={"order_id": order.id})


def _request_refund(db: Session, payment: Payment, *, reason: str) -> Refund:
    """Запрос возврата в PSP. Если PSP недоступен — фиксируем инцидент для ручного разбора."""
    refund = payment.refund or Refund(payment_id=payment.id, amount_kopecks=payment.amount_kopecks, reason=reason)
    db.add(refund)
    try:
        psp = psp_client.create_refund(
            payment.provider_payment_id, payment.amount_kopecks, idempotency_key=f"refund-{payment.id}-{uuid.uuid4()}"
        )
    except psp_client.PSPError as exc:
        refund.status = "request_failed"
        refund.last_error = str(exc)
        incident(db, "refund_request_failed", payment.order_id, error=str(exc))
        return refund
    refund.provider_refund_id = psp["refund_id"]
    refund.status = "pending"
    refund.last_error = None
    payment.status = "refund_pending"
    return refund


def _handle_refund_event(db: Session, event: dict) -> str:
    refund = db.scalar(
        select(Refund)
        .where(Refund.provider_refund_id == event.get("refund_id"))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if refund is None:
        # Вебхук мог обогнать коммит нашей транзакции, где сохранён refund_id.
        # Отвечаем ошибкой — PSP повторит доставку через пару секунд.
        log.warning("Вебхук по неизвестному возврату %s, просим PSP повторить", event.get("refund_id"))
        raise AppError(404, "UNKNOWN_REFUND", "Возврат не найден, повторите позже")
    if refund.status in {"succeeded", "failed"}:
        return "duplicate"

    payment = db.get(Payment, refund.payment_id)
    order = db.scalar(
        select(Order).where(Order.id == payment.order_id).with_for_update().execution_options(populate_existing=True)
    )
    user = db.get(User, order.user_id)

    if event["type"] == "refund.failed":
        refund.status = "failed"
        payment.status = "succeeded"
        incident(db, "refund_failed", order.id)
        return "refund_failed"

    refund.status = "succeeded"
    payment.status = "refunded"
    if refund.reason == "admin":
        transition(db, order, "refunded", actor="psp", reason="refund_completed")
        stock = lock_stock(db, [i.product_id for i in order.items])
        for item in order.items:
            stock[item.product_id].quantity += item.quantity  # товар возвращается на склад
        messaging.publish_order_event(db, order, "OrderRefunded")
        messaging.send_notification(db, user, "order_refunded", order)
    else:
        # Возврат поздней/лишней оплаты: заказ не меняется, покупателю сообщаем о возврате денег
        messaging.send_notification(db, user, "payment_refunded", order, {"reason": refund.reason})
    return "refunded"


# ---------- Возврат администратором ----------


def admin_refund(db: Session, order_id: int) -> dict:
    order = db.scalar(
        select(Order).where(Order.id == order_id).with_for_update().execution_options(populate_existing=True)
    )
    if order is None:
        raise not_found("Заказ")
    if order.status != "paid":
        raise invalid_transition(order.status, "refunded")
    payment = db.scalar(
        select(Payment)
        .where(Payment.order_id == order.id, Payment.status.in_(("succeeded", "refund_pending")))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if payment is None:
        raise AppError(409, "NO_PAYMENT", "У заказа нет успешного платежа")
    if payment.refund and payment.refund.status == "pending":
        refund = payment.refund  # повторный запрос не создаёт второй возврат (US-18 AC 4)
    else:
        refund = _request_refund(db, payment, reason="admin")
        if refund.status == "request_failed":
            db.rollback()
            raise AppError(503, "PSP_UNAVAILABLE", "Платёжный провайдер недоступен, попробуйте позже")
    db.commit()
    return {
        "order_id": order.id,
        "refund_id": refund.id,
        "status": refund.status,
        "amount_kopecks": refund.amount_kopecks,
    }
