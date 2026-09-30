"""US-15, US-16 и обработчики воркеров: outbox relay, уведомления, витрина, аудит."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models import AuditEvent, Notification, OrdersDaily, OutboxEvent, SalesDaily, StockLog
from app.services.statuses import ALLOWED_TRANSITIONS, can_transition
from tests.helpers import API, add_to_cart, place_order
from workers import analytics, audit, notifications
from workers.outbox_relay import Relay

# ---------- Администрирование ----------


def test_admin_product_lifecycle(client, db, admin, make_product):
    _, headers = admin
    category_id = client.post(f"{API}/admin/categories", json={"name": "Ноутбуки"}, headers=headers).json()["id"]
    body = {"category_id": category_id, "sku": "NB-1", "name": "Ноутбук", "price_kopecks": 50_000_00, "quantity": 3}
    product = client.post(f"{API}/admin/products", json=body, headers=headers)
    assert product.status_code == 201
    assert client.post(f"{API}/admin/products", json=body, headers=headers).json()["error"]["code"] == "SKU_TAKEN"

    pid = product.json()["id"]
    r = client.patch(f"{API}/admin/products/{pid}", json={"price_kopecks": 45_000_00}, headers=headers)
    assert r.json()["price_kopecks"] == 45_000_00
    assert client.delete(f"{API}/admin/products/{pid}", headers=headers).status_code == 204
    assert client.get(f"{API}/products/{pid}").status_code == 404


def test_stock_cannot_go_below_reserved(client, db, admin, make_product):
    admin_user, headers = admin
    product = make_product(quantity=5, reserved=3)
    r = client.put(f"{API}/admin/stock/{product.id}", json={"quantity": 2}, headers=headers)
    assert r.status_code == 409 and r.json()["error"]["code"] == "STOCK_BELOW_RESERVED"
    r = client.put(f"{API}/admin/stock/{product.id}", json={"quantity": 10}, headers=headers)
    assert r.json() == {"product_id": product.id, "quantity": 10, "reserved": 3, "available": 7}
    log = db.scalar(select(StockLog))
    assert (log.old_quantity, log.new_quantity, log.changed_by) == (5, 10, admin_user.id)


def test_price_change_does_not_touch_existing_orders(client, buyer, admin, make_product):
    _, headers = buyer
    _, admin_headers = admin
    product = make_product(price_kopecks=1_000_00)
    add_to_cart(client, headers, product.id)
    order = place_order(client, headers).json()
    client.patch(f"{API}/admin/products/{product.id}", json={"price_kopecks": 9_999_00}, headers=admin_headers)
    assert client.get(f"{API}/orders/{order['id']}", headers=headers).json()["total_kopecks"] == 1_000_00


def test_status_model():
    assert can_transition("created", "paid")
    assert can_transition("expired", "paid")  # поздняя оплата
    assert not can_transition("paid", "cancelled")
    assert not can_transition("delivered", "refunded")
    assert all(not ALLOWED_TRANSITIONS[s] for s in ("delivered", "cancelled", "refunded"))


# ---------- Outbox relay ----------


def _outbox(db, destination: str, n: int = 1):
    for i in range(n):
        db.add(
            OutboxEvent(
                destination=destination,
                topic="t",
                key="1",
                message_type=f"{destination}-{i}",
                payload={"i": i},
            )
        )
    db.commit()


def test_relay_marks_sent_and_keeps_order_on_failure(db, monkeypatch):
    _outbox(db, "kafka", 3)
    _outbox(db, "rabbitmq", 2)
    relay = Relay()
    sent = []

    def flaky_kafka(row):
        if row.message_type == "kafka-1":
            raise RuntimeError("Kafka недоступна")
        sent.append(row.message_type)

    monkeypatch.setattr(relay, "publish_kafka", flaky_kafka)
    monkeypatch.setattr(relay, "publish_rabbit", lambda row: sent.append(row.message_type))

    assert relay.run_once() == 3
    # kafka-2 не отправлен, чтобы не обогнать kafka-1; RabbitMQ при этом работает
    assert sent == ["kafka-0", "rabbitmq-0", "rabbitmq-1"]
    db.expire_all()
    failed = db.scalar(select(OutboxEvent).where(OutboxEvent.message_type == "kafka-1"))
    assert failed.sent_at is None and failed.attempts == 1

    # Kafka «на паузе» после ошибки: в следующем проходе её не трогаем
    monkeypatch.setattr(relay, "publish_kafka", lambda row: sent.append(row.message_type))
    assert relay.run_once() == 0
    relay._down_until.clear()  # прошло 10 секунд
    assert relay.run_once() == 2
    assert sent[-2:] == ["kafka-1", "kafka-2"]


# ---------- Уведомления ----------


def _command(email="a@example.com", template="receipt"):
    return {
        "command_id": str(uuid.uuid4()),
        "command_type": "SendNotification",
        "payload": {
            "template": template,
            "email": email,
            "order_id": 1,
            "order": {
                "total_kopecks": 1_000_00,
                "items": [{"product_name": "Товар", "quantity": 1, "price_kopecks": 1_000_00}],
            },
            "data": {},
        },
    }


def test_notification_sent_once(db):
    command = _command()
    assert notifications.handle_command(db, command) is True
    assert notifications.handle_command(db, command) is False  # дубль команды
    sent = db.scalars(select(Notification)).all()
    assert len(sent) == 1
    assert "1 000,00 ₽" in sent[0].body


def test_notification_failure_raises_for_retry(db):
    with pytest.raises(notifications.DeliveryError):
        notifications.handle_command(db, _command(email="fail-buyer@example.com"))


# ---------- Витрина и аудит ----------


def _event(event_type: str, items: list[dict] | None = None) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "version": 1,
        "occurred_at": datetime(2026, 9, 1, 12, tzinfo=UTC).isoformat(),
        "order_id": 1,
        "payload": {"items": items or []},
    }


ITEMS = [{"product_id": 7, "product_name": "Наушники", "quantity": 2, "price_kopecks": 5_000_00}]


def test_analytics_builds_sales_and_ignores_duplicates(db):
    created, paid = _event("OrderCreated", ITEMS), _event("OrderPaid", ITEMS)
    for event in (created, paid, paid):  # paid пришёл дважды
        analytics.handle_event(db, event)

    sales = db.scalar(select(SalesDaily))
    assert (sales.orders_count, sales.units, sales.revenue_kopecks) == (1, 2, 10_000_00)
    funnel = db.scalar(select(OrdersDaily))
    assert (funnel.created, funnel.paid) == (1, 1)

    analytics.handle_event(db, _event("OrderRefunded", ITEMS))
    db.expire_all()
    sales = db.scalar(select(SalesDaily))
    assert (sales.orders_count, sales.units, sales.revenue_kopecks) == (0, 0, 0)


def test_audit_keeps_every_event_once(db):
    event = _event("OrderPaid", ITEMS)
    assert audit.handle_event(db, event) is True
    assert audit.handle_event(db, event) is False
    assert len(db.scalars(select(AuditEvent)).all()) == 1
