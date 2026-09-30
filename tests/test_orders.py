"""US-07, US-08, US-09, US-13: оформление, резерв, гонки, отмена, истечение."""

import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.errors import AppError
from app.models import Cart, CartItem, Order, OutboxEvent, Stock, User
from app.services import orders as order_service
from tests.helpers import API, add_to_cart, outbox_types, place_order


def test_place_order_reserves_and_clears_cart(client, db, buyer, make_product):
    _, headers = buyer
    product = make_product(price_kopecks=1_000_00, quantity=5, reserved=3)
    add_to_cart(client, headers, product.id, 2)

    r = place_order(client, headers)
    assert r.status_code == 201
    order = r.json()
    assert order["status"] == "created"
    assert order["total_kopecks"] == 2_000_00
    assert order["items"][0]["price_kopecks"] == 1_000_00
    assert order["history"][0]["to_status"] == "created"

    db.expire_all()
    assert db.get(Stock, product.id).reserved == 5
    assert client.get(f"{API}/cart", headers=headers).json()["items"] == []
    assert outbox_types(db) == ["OrderCreated", "ExpireReservation"]
    delayed = db.scalar(select(OutboxEvent).where(OutboxEvent.message_type == "ExpireReservation"))
    assert delayed.destination == "rabbitmq" and delayed.delay_ms == 900_000


def test_out_of_stock_changes_nothing(client, db, buyer, make_product):
    _, headers = buyer
    product = make_product(quantity=5, reserved=3)
    add_to_cart(client, headers, product.id, 2)
    db.get(Stock, product.id).reserved = 4  # кто-то успел зарезервировать
    db.commit()

    r = place_order(client, headers)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "OUT_OF_STOCK"
    assert r.json()["error"]["details"] == [{"product_id": product.id, "requested": 2, "available": 1}]
    db.expire_all()
    assert db.get(Stock, product.id).reserved == 4
    assert db.scalar(select(Order.id)) is None
    assert len(client.get(f"{API}/cart", headers=headers).json()["items"]) == 1


def test_empty_cart(client, buyer):
    _, headers = buyer
    r = place_order(client, headers)
    assert r.status_code == 422 and r.json()["error"]["code"] == "CART_EMPTY"


def test_same_idempotency_key_returns_same_order(client, db, buyer, make_product):
    _, headers = buyer
    product = make_product(quantity=5)
    add_to_cart(client, headers, product.id, 1)
    first = place_order(client, headers, key="key-1")
    second = place_order(client, headers, key="key-1")
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    db.expire_all()
    assert db.get(Stock, product.id).reserved == 1
    assert len(db.scalars(select(Order)).all()) == 1


def test_failed_request_does_not_burn_idempotency_key(client, buyer, make_product):
    _, headers = buyer
    assert place_order(client, headers, key="k").status_code == 422  # пустая корзина
    add_to_cart(client, headers, make_product().id)
    assert place_order(client, headers, key="k").status_code == 201


def test_race_for_last_item(db, make_product, make_user):
    """20 покупателей одновременно берут последний товар — успешен ровно один (G1)."""
    product = make_product(quantity=5, reserved=4)
    users = []
    for _ in range(20):
        user, _ = make_user()
        cart = Cart(user_id=user.id)
        cart.items.append(CartItem(product_id=product.id, quantity=1))
        db.add(cart)
        users.append(user.id)
    db.commit()

    results: list[str] = []
    barrier = threading.Barrier(20)

    def buy(user_id: int):
        with SessionLocal() as session:
            user = session.get(User, user_id)
            barrier.wait()  # стартуем одновременно
            try:
                order_service.create_order(session, user)
                session.commit()
                results.append("ok")
            except AppError as exc:
                session.rollback()
                results.append(exc.code)

    threads = [threading.Thread(target=buy, args=(uid,)) for uid in users]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("ok") == 1
    assert results.count("OUT_OF_STOCK") == 19
    db.expire_all()
    assert db.get(Stock, product.id).reserved == 5


def test_cancel_releases_reservation_and_is_idempotent(client, db, buyer, make_product):
    _, headers = buyer
    product = make_product(quantity=5)
    add_to_cart(client, headers, product.id, 2)
    order_id = place_order(client, headers).json()["id"]

    r = client.post(f"{API}/orders/{order_id}/cancel", headers=headers)
    assert r.json()["status"] == "cancelled"
    assert client.post(f"{API}/orders/{order_id}/cancel", headers=headers).status_code == 200
    db.expire_all()
    assert db.get(Stock, product.id).reserved == 0
    assert outbox_types(db)[-1] == "OrderCancelled"


def test_cannot_cancel_expired_order(client, db, buyer, make_product):
    _, headers = buyer
    add_to_cart(client, headers, make_product().id)
    order_id = place_order(client, headers).json()["id"]
    db.get(Order, order_id).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    order_service.expire_order(db, order_id, source="test")

    r = client.post(f"{API}/orders/{order_id}/cancel", headers=headers)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "INVALID_TRANSITION"


def test_foreign_order_is_404(client, buyer, make_user, make_product):
    _, headers = buyer
    _, other_headers = make_user()
    add_to_cart(client, headers, make_product().id)
    order_id = place_order(client, headers).json()["id"]
    assert client.get(f"{API}/orders/{order_id}", headers=other_headers).status_code == 404


def test_list_orders_filter(client, buyer, make_product):
    _, headers = buyer
    product = make_product(quantity=10)
    for _ in range(2):
        add_to_cart(client, headers, product.id)
        place_order(client, headers)
    first_id = client.get(f"{API}/orders", headers=headers).json()["items"][-1]["id"]
    client.post(f"{API}/orders/{first_id}/cancel", headers=headers)

    assert client.get(f"{API}/orders", headers=headers).json()["total"] == 2
    created = client.get(f"{API}/orders", params={"status": "created"}, headers=headers).json()
    assert created["total"] == 1


def test_expire_order(client, db, buyer, make_product):
    _, headers = buyer
    product = make_product(quantity=5)
    add_to_cart(client, headers, product.id, 2)
    order_id = place_order(client, headers).json()["id"]

    assert order_service.expire_order(db, order_id, source="test") is False  # срок ещё не вышел

    db.get(Order, order_id).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert order_service.find_overdue_order_ids(db) == [order_id]
    assert order_service.expire_order(db, order_id, source="test") is True
    assert order_service.expire_order(db, order_id, source="test") is False  # повтор безопасен

    db.expire_all()
    assert db.get(Order, order_id).status == "expired"
    assert db.get(Stock, product.id).reserved == 0
    assert outbox_types(db)[-2:] == ["OrderExpired", "SendNotification"]
