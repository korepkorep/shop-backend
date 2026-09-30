import hashlib
import hmac
import json
import time
import uuid

from sqlalchemy import select

from app.models import OutboxEvent

API = "/api/v1"


def add_to_cart(client, headers, product_id: int, quantity: int = 1):
    return client.post(f"{API}/cart/items", json={"product_id": product_id, "quantity": quantity}, headers=headers)


def place_order(client, headers, key: str | None = None):
    return client.post(f"{API}/orders", headers={**headers, "Idempotency-Key": key or str(uuid.uuid4())})


def create_payment(client, headers, order_id: int, key: str | None = None):
    return client.post(
        f"{API}/orders/{order_id}/payments", headers={**headers, "Idempotency-Key": key or str(uuid.uuid4())}
    )


def signed_webhook(client, event: dict, secret: str = "test-secret", timestamp: int | None = None, signature=None):
    body = json.dumps(event).encode()
    ts = str(timestamp or int(time.time()))
    sig = signature or hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return client.post(
        f"{API}/webhooks/psp",
        content=body,
        headers={"Content-Type": "application/json", "X-PSP-Timestamp": ts, "X-PSP-Signature": sig},
    )


def payment_event(payment_id: str, amount: int, status: str = "succeeded") -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "type": f"payment.{status}",
        "payment_id": payment_id,
        "amount_kopecks": amount,
    }


def outbox_types(db) -> list[str]:
    db.expire_all()
    return [e.message_type for e in db.scalars(select(OutboxEvent).order_by(OutboxEvent.id))]


def ordered_and_paid(client, headers, psp, product, quantity: int = 1) -> dict:
    """Готовый сценарий: товар в корзине → заказ → платёж → вебхук об оплате."""
    add_to_cart(client, headers, product.id, quantity)
    order = place_order(client, headers).json()
    payment = create_payment(client, headers, order["id"]).json()
    assert (
        signed_webhook(client, payment_event(payment["provider_payment_id"], order["total_kopecks"])).status_code == 200
    )
    return {"order": order, "payment": payment}
