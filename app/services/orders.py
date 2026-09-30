"""Заказы: оформление с резервом, просмотр, отмена, истечение (US-07, US-08, US-09, US-13)."""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError, not_found
from app.models import Cart, Order, OrderItem, Product, Stock, User
from app.services import messaging
from app.services.statuses import record_creation, transition

log = logging.getLogger(__name__)


def lock_stock(db: Session, product_ids: list[int]) -> dict[int, Stock]:
    """Блокирует строки склада до конца транзакции (SELECT ... FOR UPDATE).

    Сортировка по product_id обязательна: если две транзакции блокируют одни и те же
    товары в разном порядке, они могут навсегда ждать друг друга (deadlock).
    populate_existing: после получения блокировки перечитываем строку из БД,
    а не берём устаревшую копию из памяти сессии.
    """
    rows = db.scalars(
        select(Stock)
        .where(Stock.product_id.in_(product_ids))
        .order_by(Stock.product_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    return {s.product_id: s for s in rows}


def serialize_order(order: Order) -> dict:
    return {
        "id": order.id,
        "user_id": order.user_id,
        "status": order.status,
        "total_kopecks": order.total_kopecks,
        "expires_at": order.expires_at,
        "created_at": order.created_at,
        "items": [
            {
                "product_id": i.product_id,
                "product_name": i.product_name,
                "quantity": i.quantity,
                "price_kopecks": i.price_kopecks,
            }
            for i in order.items
        ],
        "history": [
            {
                "from_status": h.from_status,
                "to_status": h.to_status,
                "actor": h.actor,
                "reason": h.reason,
                "created_at": h.created_at,
            }
            for h in order.history
        ],
    }


def create_order(db: Session, user: User) -> tuple[int, dict]:
    """Оформление заказа. Всё в одной транзакции: либо заказ создан и товар
    зарезервирован, либо ничего не изменилось. Коммит делает вызывающий код
    (run_idempotent), чтобы ключ идемпотентности сохранился вместе с заказом."""
    # Блокируем корзину: два параллельных оформления одной корзины не создадут два заказа
    cart = db.scalar(
        select(Cart).where(Cart.user_id == user.id).with_for_update().execution_options(populate_existing=True)
    )
    if cart is None or not cart.items:
        raise AppError(422, "CART_EMPTY", "Корзина пуста")

    product_ids = sorted(i.product_id for i in cart.items)
    stock = lock_stock(db, product_ids)
    products = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(product_ids)))}

    inactive, shortage = [], []
    for item in cart.items:
        product = products[item.product_id]
        available = stock[item.product_id].available if item.product_id in stock else 0
        if not product.is_active:
            inactive.append({"product_id": product.id})
        elif available < item.quantity:
            shortage.append({"product_id": product.id, "requested": item.quantity, "available": available})
    if inactive:
        raise AppError(409, "PRODUCT_INACTIVE", "В корзине есть товары, снятые с продажи", inactive)
    if shortage:
        raise AppError(409, "OUT_OF_STOCK", "Недостаточно товара на складе", shortage)

    now = datetime.now(UTC)
    order = Order(
        user_id=user.id,
        status="created",
        total_kopecks=sum(products[i.product_id].price_kopecks * i.quantity for i in cart.items),
        expires_at=now + timedelta(seconds=settings.reservation_ttl_seconds),
    )
    for item in cart.items:
        product = products[item.product_id]
        # BR-02: название и цена копируются в заказ
        order.items.append(
            OrderItem(
                product_id=product.id,
                product_name=product.name,
                quantity=item.quantity,
                price_kopecks=product.price_kopecks,
            )
        )
        stock[item.product_id].reserved += item.quantity
    db.add(order)
    db.flush()  # получаем order.id

    record_creation(db, order)
    db.delete(cart)  # корзина очищается (US-07 AC 6)
    messaging.publish_order_event(db, order, "OrderCreated", {"expires_at": order.expires_at.isoformat()})
    messaging.schedule_reservation_expiry(db, order)
    db.flush()
    db.refresh(order)
    log.info("Заказ оформлен", extra={"order_id": order.id})
    return 201, serialize_order(order)


def get_user_order(db: Session, user: User, order_id: int, for_update: bool = False) -> Order:
    query = select(Order).where(Order.id == order_id, Order.user_id == user.id)
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    order = db.scalar(query)
    # Чужой заказ → 404, а не 403: не раскрываем, что такой заказ существует (US-08 AC 3)
    if order is None:
        raise not_found("Заказ")
    return order


def list_user_orders(db: Session, user: User, status: str | None, limit: int, offset: int) -> dict:
    query = select(Order).where(Order.user_id == user.id)
    if status:
        query = query.where(Order.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    orders = db.scalars(query.order_by(Order.created_at.desc(), Order.id.desc()).limit(limit).offset(offset)).all()
    return {
        "items": [
            {
                "id": o.id,
                "status": o.status,
                "total_kopecks": o.total_kopecks,
                "expires_at": o.expires_at,
                "created_at": o.created_at,
            }
            for o in orders
        ],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def release_reservation(db: Session, order: Order) -> None:
    stock = lock_stock(db, [i.product_id for i in order.items])
    for item in order.items:
        stock[item.product_id].reserved -= item.quantity


def cancel_order(db: Session, user: User, order_id: int) -> dict:
    order = get_user_order(db, user, order_id, for_update=True)
    if order.status == "cancelled":
        return serialize_order(order)  # повторная отмена — не ошибка (US-09 AC 2)
    transition(db, order, "cancelled", actor="customer", reason="cancelled_by_customer")
    release_reservation(db, order)
    messaging.publish_order_event(db, order, "OrderCancelled", {"reason": "cancelled_by_customer"})
    db.commit()
    db.refresh(order)
    return serialize_order(order)


def expire_order(db: Session, order_id: int, *, source: str) -> bool:
    """Переводит неоплаченный просроченный заказ в expired (US-13).
    Вызывается воркером reservations (по TTL из RabbitMQ) и воркером sweeper (страховка).
    Безопасен при повторном вызове. Возвращает True, если заказ действительно истёк."""
    order = db.scalar(
        select(Order).where(Order.id == order_id).with_for_update().execution_options(populate_existing=True)
    )
    if order is None or order.status != "created":
        db.rollback()
        return False  # уже оплачен, отменён или истёк — ничего не делаем
    if order.expires_at > datetime.now(UTC):
        db.rollback()
        return False  # срок ещё не вышел
    transition(db, order, "expired", actor="system", reason=f"payment_timeout:{source}")
    release_reservation(db, order)
    user = db.get(User, order.user_id)
    messaging.publish_order_event(db, order, "OrderExpired", {"reason": "payment_timeout"})
    messaging.send_notification(db, user, "order_expired", order)  # BR-11
    db.commit()
    log.info("Заказ истёк, резерв снят", extra={"order_id": order.id})
    return True


def find_overdue_order_ids(db: Session, limit: int = 500) -> list[int]:
    return list(
        db.scalars(
            select(Order.id)
            .where(Order.status == "created", Order.expires_at <= datetime.now(UTC))
            .order_by(Order.expires_at)
            .limit(limit)
        )
    )
