"""Статусная модель заказа. Любой переход — только через transition(): он проверяет,
что переход разрешён, и пишет его в историю."""

from sqlalchemy.orm import Session

from app.core.errors import invalid_transition
from app.models import Order, OrderStatusHistory

# Откуда → куда можно перейти (см. 01-vision.md, «Статусы заказа»)
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "created": {"paid", "cancelled", "expired"},
    "paid": {"shipped", "refunded"},
    "shipped": {"delivered"},
    "expired": {"paid"},  # только поздняя оплата (BR-10)
    "delivered": set(),
    "cancelled": set(),
    "refunded": set(),
}


def can_transition(current: str, target: str) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, set())


def transition(db: Session, order: Order, target: str, *, actor: str, reason: str) -> None:
    if not can_transition(order.status, target):
        raise invalid_transition(order.status, target)
    db.add(
        OrderStatusHistory(order_id=order.id, from_status=order.status, to_status=target, actor=actor, reason=reason)
    )
    order.status = target


def record_creation(db: Session, order: Order, actor: str = "customer") -> None:
    db.add(
        OrderStatusHistory(order_id=order.id, from_status=None, to_status=order.status, actor=actor, reason="created")
    )
