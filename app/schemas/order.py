from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class OrderStatus(StrEnum):
    created = "created"
    paid = "paid"
    shipped = "shipped"
    delivered = "delivered"
    cancelled = "cancelled"
    expired = "expired"
    refunded = "refunded"


class OrderItemOut(BaseModel):
    product_id: int
    product_name: str
    quantity: int
    price_kopecks: int


class StatusHistoryOut(BaseModel):
    from_status: str | None
    to_status: str
    actor: str
    reason: str
    created_at: datetime


class OrderShort(BaseModel):
    id: int
    status: str
    total_kopecks: int
    expires_at: datetime
    created_at: datetime


class OrderOut(OrderShort):
    user_id: int
    items: list[OrderItemOut]
    history: list[StatusHistoryOut]


class OrderPage(BaseModel):
    items: list[OrderShort]
    total: int
    limit: int
    offset: int


class PaymentOut(BaseModel):
    id: int
    order_id: int
    provider_payment_id: str
    amount_kopecks: int
    status: str
    payment_url: str
