"""Таблицы консьюмеров Kafka. Живут в отдельных схемах: analytics и audit.
Магазин в них не пишет — только воркеры, которые читают события."""

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SalesDaily(Base):
    """Витрина продаж по дням и товарам (US-19). Возвраты уменьшают показатели."""

    __tablename__ = "sales_daily"
    __table_args__ = {"schema": "analytics"}

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    product_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    product_name: Mapped[str] = mapped_column(String(200))
    orders_count: Mapped[int] = mapped_column(Integer, default=0)
    units: Mapped[int] = mapped_column(Integer, default=0)
    revenue_kopecks: Mapped[int] = mapped_column(BigInteger, default=0)


class OrdersDaily(Base):
    """Воронка заказов по дням: сколько оформлено, оплачено, истекло, отменено, возвращено."""

    __tablename__ = "orders_daily"
    __table_args__ = {"schema": "analytics"}

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    created: Mapped[int] = mapped_column(Integer, default=0)
    paid: Mapped[int] = mapped_column(Integer, default=0)
    expired: Mapped[int] = mapped_column(Integer, default=0)
    cancelled: Mapped[int] = mapped_column(Integer, default=0)
    refunded: Mapped[int] = mapped_column(Integer, default=0)


class AnalyticsProcessedEvent(Base):
    __tablename__ = "processed_events"
    __table_args__ = {"schema": "analytics"}

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class AuditEvent(Base):
    """Бессрочный журнал всех событий заказов. PK по event_id — дубль просто не вставится."""

    __tablename__ = "event_log"
    __table_args__ = {"schema": "audit"}

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    order_id: Mapped[int] = mapped_column(BigInteger, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSONB)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
