"""US-10, US-11, US-12, US-18: оплата, вебхуки, поздняя оплата, возвраты."""

import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import Incident, Order, Payment, Refund, Stock
from app.services import orders as order_service
from tests.helpers import (
    API,
    add_to_cart,
    create_payment,
    ordered_and_paid,
    outbox_types,
    payment_event,
    place_order,
    signed_webhook,
)


def _order(client, headers, product, quantity=1):
    add_to_cart(client, headers, product.id, quantity)
    return place_order(client, headers).json()


def test_create_payment_and_reuse_pending(client, buyer, make_product, psp):
    _, headers = buyer
    order = _order(client, headers, make_product(price_kopecks=500_00))
    first = create_payment(client, headers, order["id"])
    assert first.status_code == 201
    assert first.json()["amount_kopecks"] == 500_00
    second = create_payment(client, headers, order["id"])
    assert second.status_code == 200  # BR-06: активный платёж уже есть
    assert second.json()["id"] == first.json()["id"]
    assert len(psp.payments) == 1


def test_psp_unavailable(client, db, buyer, make_product, psp):
    _, headers = buyer
    order = _order(client, headers, make_product())
    psp.available = False
    r = create_payment(client, headers, order["id"])
    assert r.status_code == 503 and r.json()["error"]["code"] == "PSP_UNAVAILABLE"
    db.expire_all()
    assert db.get(Order, order["id"]).status == "created"


def test_cannot_pay_expired_order(client, db, buyer, make_product, psp):
    _, headers = buyer
    order = _order(client, headers, make_product())
    db.get(Order, order["id"]).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert create_payment(client, headers, order["id"]).json()["error"]["code"] == "ORDER_EXPIRED"


def test_successful_payment(client, db, buyer, make_product, psp):
    _, headers = buyer
    product = make_product(quantity=5)
    result = ordered_and_paid(client, headers, psp, product, quantity=2)
    order_id = result["order"]["id"]

    db.expire_all()
    assert db.get(Order, order_id).status == "paid"
    stock = db.get(Stock, product.id)
    assert (stock.quantity, stock.reserved) == (3, 0)
    assert outbox_types(db)[-2:] == ["OrderPaid", "SendNotification"]


def test_duplicate_webhook_changes_nothing(client, db, buyer, make_product, psp):
    _, headers = buyer
    product = make_product(quantity=5)
    result = ordered_and_paid(client, headers, psp, product)
    before = outbox_types(db)

    event = payment_event(result["payment"]["provider_payment_id"], result["order"]["total_kopecks"])
    r = signed_webhook(client, event)
    assert r.status_code == 200 and r.json()["result"] == "duplicate"
    db.expire_all()
    assert db.get(Stock, product.id).quantity == 4
    assert outbox_types(db) == before


def test_bad_signature_and_stale_webhook(client, db, buyer, make_product, psp):
    _, headers = buyer
    order = _order(client, headers, make_product())
    payment = create_payment(client, headers, order["id"]).json()
    event = payment_event(payment["provider_payment_id"], order["total_kopecks"])

    assert signed_webhook(client, event, secret="wrong").status_code == 401
    assert signed_webhook(client, event, timestamp=int(time.time()) - 600).status_code == 401
    db.expire_all()
    assert db.get(Order, order["id"]).status == "created"


def test_amount_mismatch(client, db, buyer, make_product, psp):
    _, headers = buyer
    order = _order(client, headers, make_product(price_kopecks=1_000_00))
    payment = create_payment(client, headers, order["id"]).json()
    r = signed_webhook(client, payment_event(payment["provider_payment_id"], 1_00))
    assert r.status_code == 200 and r.json()["result"] == "amount_mismatch"
    db.expire_all()
    assert db.get(Order, order["id"]).status == "created"
    assert db.scalar(select(Incident.kind)) == "amount_mismatch"


def test_failed_payment_allows_retry(client, db, buyer, make_product, psp):
    _, headers = buyer
    order = _order(client, headers, make_product())
    payment = create_payment(client, headers, order["id"]).json()
    signed_webhook(client, payment_event(payment["provider_payment_id"], order["total_kopecks"], status="failed"))
    db.expire_all()
    assert db.get(Order, order["id"]).status == "created"
    retry = create_payment(client, headers, order["id"])
    assert retry.status_code == 201 and retry.json()["id"] != payment["id"]


def test_unknown_payment_is_logged_and_acknowledged(client, db):
    r = signed_webhook(client, payment_event("pay_nope", 100))
    assert r.status_code == 200 and r.json()["result"] == "unknown_payment"
    assert db.scalar(select(Incident.kind)) == "unknown_payment"


def _expire(db, order_id):
    db.get(Order, order_id).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert order_service.expire_order(db, order_id, source="test")


def test_late_payment_restores_order_when_stock_left(client, db, buyer, make_product, psp):
    _, headers = buyer
    product = make_product(quantity=5)
    order = _order(client, headers, product, 2)
    payment = create_payment(client, headers, order["id"]).json()
    _expire(db, order["id"])

    r = signed_webhook(client, payment_event(payment["provider_payment_id"], order["total_kopecks"]))
    assert r.json()["result"] == "paid_late"
    db.expire_all()
    assert db.get(Order, order["id"]).status == "paid"
    stock = db.get(Stock, product.id)
    assert (stock.quantity, stock.reserved) == (3, 0)


def test_late_payment_refunded_when_stock_gone(client, db, buyer, make_product, psp):
    _, headers = buyer
    product = make_product(quantity=1)
    order = _order(client, headers, product)
    payment = create_payment(client, headers, order["id"]).json()
    _expire(db, order["id"])
    db.get(Stock, product.id).reserved = 1  # последний товар успел купить другой
    db.commit()

    r = signed_webhook(client, payment_event(payment["provider_payment_id"], order["total_kopecks"]))
    assert r.json()["result"] == "refund_requested"
    db.expire_all()
    assert db.get(Order, order["id"]).status == "expired"
    refund = db.scalar(select(Refund))
    assert (refund.reason, refund.status) == ("late_payment", "pending")
    assert len(psp.refunds) == 1

    # PSP подтверждает возврат — покупатель получает письмо, заказ не меняется
    r = signed_webhook(client, {"event_id": str(uuid.uuid4()), "type": "refund.succeeded", "refund_id": "ref_1"})
    assert r.json()["result"] == "refunded"
    db.expire_all()
    assert db.scalar(select(Payment.status)) == "refunded"
    assert outbox_types(db)[-1] == "SendNotification"


def test_payment_for_cancelled_order_is_refunded(client, db, buyer, make_product, psp):
    _, headers = buyer
    order = _order(client, headers, make_product())
    payment = create_payment(client, headers, order["id"]).json()
    client.post(f"{API}/orders/{order['id']}/cancel", headers=headers)
    r = signed_webhook(client, payment_event(payment["provider_payment_id"], order["total_kopecks"]))
    assert r.json()["result"] == "refund_requested"
    assert db.scalar(select(Refund.reason)) == "cancelled_order"


def test_admin_refund_flow(client, db, buyer, admin, make_product, psp):
    _, headers = buyer
    _, admin_headers = admin
    product = make_product(quantity=5)
    order_id = ordered_and_paid(client, headers, psp, product, 2)["order"]["id"]

    r = client.post(f"{API}/admin/orders/{order_id}/refund", headers=admin_headers)
    assert r.status_code == 202 and r.json()["status"] == "pending"
    again = client.post(f"{API}/admin/orders/{order_id}/refund", headers=admin_headers)
    assert again.json()["refund_id"] == r.json()["refund_id"]
    assert len(psp.refunds) == 1  # второй возврат не создан
    db.expire_all()
    assert db.get(Order, order_id).status == "paid"  # до подтверждения PSP статус не меняется

    signed_webhook(client, {"event_id": str(uuid.uuid4()), "type": "refund.succeeded", "refund_id": "ref_1"})
    db.expire_all()
    assert db.get(Order, order_id).status == "refunded"
    assert db.get(Stock, product.id).quantity == 5  # товар вернулся на склад
    assert "OrderRefunded" in outbox_types(db)


def test_unknown_refund_asks_psp_to_retry(client):
    r = signed_webhook(client, {"event_id": "x", "type": "refund.succeeded", "refund_id": "ref_unknown"})
    assert r.status_code == 404


def test_ship_and_deliver(client, buyer, admin, make_product, psp):
    _, headers = buyer
    _, admin_headers = admin
    order_id = ordered_and_paid(client, headers, psp, make_product())["order"]["id"]
    assert client.post(f"{API}/admin/orders/{order_id}/deliver", headers=admin_headers).status_code == 409
    assert client.post(f"{API}/admin/orders/{order_id}/ship", headers=admin_headers).json()["status"] == "shipped"
    assert client.post(f"{API}/admin/orders/{order_id}/deliver", headers=admin_headers).json()["status"] == "delivered"
