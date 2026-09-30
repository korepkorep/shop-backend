"""Служебные таблицы: идемпотентность, outbox, обработанные сообщения, инциденты, уведомления."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin


class IdempotencyKey(CreatedAtMixin, Base):
    """Ответ на запрос с заголовком Idempotency-Key. Повтор с тем же ключом получает тот же ответ."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("user_id", "endpoint", "key", name="uq_idempotency_keys_user_endpoint_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    endpoint: Mapped[str] = mapped_column(String(128))
    key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict | None] = mapped_column(JSONB)


class OutboxEvent(CreatedAtMixin, Base):
    """Transactional outbox: сообщение пишется в одной транзакции с бизнес-данными,
    а воркер outbox_relay потом публикует его в Kafka или RabbitMQ."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint("destination IN ('kafka', 'rabbitmq')", name="destination"),
        Index("ix_outbox_unsent", "id", postgresql_where=text("sent_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, default=uuid.uuid4)
    destination: Mapped[str] = mapped_column(String(16))
    # Kafka: имя топика. RabbitMQ: routing key.
    topic: Mapped[str] = mapped_column(String(100))
    key: Mapped[str | None] = mapped_column(String(100))  # ключ партиции Kafka
    message_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB)
    delay_ms: Mapped[int | None] = mapped_column(Integer)  # отложенная доставка через TTL в RabbitMQ
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


class ProcessedMessage(Base):
    """Какие сообщения consumer уже обработал — защита от дублей (at-least-once)."""

    __tablename__ = "processed_messages"

    consumer: Mapped[str] = mapped_column(String(64), primary_key=True)
    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class Incident(CreatedAtMixin, Base):
    """Журнал ситуаций для ручного разбора: расхождение суммы, неизвестный платёж, поздняя оплата."""

    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    order_id: Mapped[int | None] = mapped_column(BigInteger)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


class Notification(CreatedAtMixin, Base):
    """Отправленные уведомления. В учебной версии письмо не уходит наружу, а сохраняется здесь и в лог."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    command_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True)
    email: Mapped[str] = mapped_column(String(255))
    template: Mapped[str] = mapped_column(String(64))
    order_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
