"""Воркер уведомлений (US-14): читает команды SendNotification из RabbitMQ и «отправляет» письма.

В учебной версии письмо не уходит наружу: оно пишется в лог и в таблицу notifications.
Ошибка отправки → 3 повтора с паузами (очереди notifications.retry.*) → parking-очередь.
Письма на адреса со словом «fail» всегда «падают» — так можно посмотреть ретраи вживую.

Запуск: python -m workers.notifications
"""

import json
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.logging import setup_logging
from app.models import Notification
from workers.brokers import (
    Q_NOTIFICATIONS,
    Q_NOTIFICATIONS_PARKING,
    persistent_properties,
    retry_queue_name,
    run_rabbit_consumer,
)

log = logging.getLogger("notifications")


class DeliveryError(Exception):
    pass


def rub(kopecks: int) -> str:
    return f"{kopecks / 100:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def render(payload: dict) -> tuple[str, str]:
    order_id = payload["order_id"]
    order = payload.get("order", {})
    total = rub(order.get("total_kopecks", 0))
    lines = "\n".join(
        f"  {i['product_name']} × {i['quantity']} — {rub(i['price_kopecks'] * i['quantity'])}"
        for i in order.get("items", [])
    )
    templates = {
        "receipt": (f"Чек по заказу №{order_id}", f"Спасибо за покупку!\n\n{lines}\n\nИтого: {total}"),
        "order_expired": (
            f"Заказ №{order_id} отменён",
            "Заказ не был оплачен вовремя и отменён. Товар снова доступен в каталоге.",
        ),
        "order_shipped": (f"Заказ №{order_id} отправлен", "Ваш заказ передан в доставку."),
        "order_refunded": (f"Возврат по заказу №{order_id}", f"Мы вернули {total} на вашу карту."),
        "payment_refunded": (
            f"Возврат оплаты по заказу №{order_id}",
            f"Оплата пришла после отмены заказа, товар уже недоступен. Мы вернули {total} на вашу карту.",
        ),
    }
    if payload["template"] not in templates:
        raise ValueError(f"Неизвестный шаблон {payload['template']}")
    return templates[payload["template"]]


def handle_command(db: Session, command: dict) -> bool:
    """Обрабатывает одну команду. Возвращает False, если это дубль (письмо уже отправлено)."""
    command_id = uuid.UUID(command["command_id"])
    if db.scalar(select(Notification.id).where(Notification.command_id == command_id)):
        log.info("Дубль команды, письмо уже отправлено", extra={"command_id": str(command_id)})
        return False

    payload = command["payload"]
    subject, body = render(payload)
    if settings.notify_fail_marker and settings.notify_fail_marker in payload["email"]:
        raise DeliveryError(f"Почтовый сервер отклонил адрес {payload['email']}")

    # «Отправка»: пишем в лог и сохраняем. В реальной системе здесь вызов SMTP / сервиса рассылок
    log.info("Письмо %s: «%s»", payload["email"], subject, extra={"order_id": payload["order_id"]})
    db.add(
        Notification(
            command_id=command_id,
            email=payload["email"],
            template=payload["template"],
            order_id=payload["order_id"],
            subject=subject,
            body=body,
        )
    )
    db.commit()
    return True


def on_message(channel, method, properties, body: bytes) -> None:
    command = json.loads(body)
    attempt = int((properties.headers or {}).get("x-attempt", 0))
    try:
        with SessionLocal() as db:
            handle_command(db, command)
    except Exception as exc:
        delays = settings.retry_delays
        if attempt < len(delays):
            target = retry_queue_name(delays[attempt])
            log.warning("Ошибка отправки (%s), повтор через %s с", exc, delays[attempt])
        else:
            target = Q_NOTIFICATIONS_PARKING
            log.error("Письмо не отправлено после %s повторов, в parking: %s", attempt, exc)
        channel.basic_publish(
            exchange="",  # default exchange: routing key = имя очереди
            routing_key=target,
            body=body,
            properties=persistent_properties(
                properties.message_id or command["command_id"],
                properties.type or "SendNotification",
                headers={"x-attempt": attempt + 1, "x-last-error": str(exc)[:200]},
            ),
        )
    # ack в любом случае: сообщение либо обработано, либо переложено в retry/parking
    channel.basic_ack(delivery_tag=method.delivery_tag)


def main() -> None:
    setup_logging(settings.log_level)
    run_rabbit_consumer(Q_NOTIFICATIONS, on_message)


if __name__ == "__main__":
    main()
