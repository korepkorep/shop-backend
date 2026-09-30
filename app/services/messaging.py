"""Запись сообщений в outbox. Публикацией в брокеры занимается воркер outbox_relay.

Два вида сообщений (см. ADR «RabbitMQ vs Kafka»):
- событие — факт «произошло» (OrderPaid). Идёт в Kafka, читают все, кому интересно;
- команда — просьба «сделай» (SendNotification). Идёт в RabbitMQ одному исполнителю.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Order, OutboxEvent, User

# Routing keys RabbitMQ
RK_NOTIFICATION = "notifications.send"
RK_EXPIRE_DELAYED = "reservations.expire.delay"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def order_items_payload(order: Order) -> list[dict]:
    return [
        {
            "product_id": i.product_id,
            "product_name": i.product_name,
            "quantity": i.quantity,
            "price_kopecks": i.price_kopecks,
        }
        for i in order.items
    ]


def publish_order_event(db: Session, order: Order, event_type: str, extra: dict | None = None) -> None:
    """Доменное событие заказа → Kafka, топик orders.events, ключ = order_id
    (все события одного заказа попадут в одну партицию и придут по порядку)."""
    event_id = uuid.uuid4()
    payload = {
        "user_id": order.user_id,
        "status": order.status,
        "total_kopecks": order.total_kopecks,
        "items": order_items_payload(order),
        **(extra or {}),
    }
    envelope = {
        "event_id": str(event_id),
        "event_type": event_type,
        "version": 1,
        "occurred_at": _now_iso(),
        "order_id": order.id,
        "payload": payload,
    }
    db.add(
        OutboxEvent(
            message_id=event_id,
            destination="kafka",
            topic=settings.orders_topic,
            key=str(order.id),
            message_type=event_type,
            payload=envelope,
        )
    )


def _command(db: Session, routing_key: str, command_type: str, payload: dict, delay_ms: int | None = None) -> None:
    command_id = uuid.uuid4()
    envelope = {
        "command_id": str(command_id),
        "command_type": command_type,
        "created_at": _now_iso(),
        "payload": payload,
    }
    db.add(
        OutboxEvent(
            message_id=command_id,
            destination="rabbitmq",
            topic=routing_key,
            message_type=command_type,
            payload=envelope,
            delay_ms=delay_ms,
        )
    )


def send_notification(db: Session, user: User, template: str, order: Order, data: dict | None = None) -> None:
    _command(
        db,
        RK_NOTIFICATION,
        "SendNotification",
        {
            "template": template,
            "email": user.email,
            "order_id": order.id,
            # Всё нужное для письма — в самой команде: воркеру уведомлений не нужно ходить в БД заказов
            "order": {"total_kopecks": order.total_kopecks, "items": order_items_payload(order)},
            "data": data or {},
        },
    )


def schedule_reservation_expiry(db: Session, order: Order) -> None:
    """Отложенная команда: RabbitMQ продержит её TTL и через DLX передаст воркеру reservations."""
    _command(
        db,
        RK_EXPIRE_DELAYED,
        "ExpireReservation",
        {"order_id": order.id},
        delay_ms=settings.reservation_ttl_seconds * 1000,
    )
