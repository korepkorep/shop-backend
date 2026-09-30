"""Outbox relay: читает неотправленные сообщения из таблицы outbox_events
и публикует события в Kafka, а команды — в RabbitMQ.

Зачем: если сохранять заказ в БД и сразу слать в брокер, то при падении между
этими шагами событие потеряется (или уйдёт событие о несохранённом заказе).
С outbox сообщение пишется в той же транзакции, что и заказ, — они не разойдутся.
Гарантия доставки — at-least-once: упали после отправки, но до отметки sent_at —
сообщение уйдёт ещё раз. Получатели к этому готовы.

Запуск: python -m workers.outbox_relay
"""

import json
import logging
import time
from datetime import UTC, datetime

import pika
from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.logging import setup_logging
from app.models import OutboxEvent
from workers.brokers import (
    COMMANDS_EXCHANGE,
    declare_topology,
    ensure_topics,
    kafka_producer,
    persistent_properties,
    rabbit_connection,
)

log = logging.getLogger("outbox_relay")
BATCH_SIZE = 100
BROKER_BACKOFF_SECONDS = 10  # после ошибки не дёргаем этот брокер 10 секунд


class Relay:
    def __init__(self):
        self._producer = None
        self._rabbit = None
        self._channel = None
        # Простейший circuit breaker: лежащий брокер не тормозит отправку в работающий
        self._down_until: dict[str, float] = {}

    # Подключения ленивые: если брокер лежит, пробуем снова на следующем цикле
    def producer(self):
        if self._producer is None:
            ensure_topics()
            self._producer = kafka_producer()
        return self._producer

    def channel(self):
        if self._channel is None or self._channel.is_closed:
            self._rabbit = rabbit_connection()
            self._channel = self._rabbit.channel()
            declare_topology(self._channel)
            self._channel.confirm_delivery()  # брокер подтверждает каждое сообщение
        return self._channel

    def publish_kafka(self, row: OutboxEvent) -> None:
        errors = []
        self.producer().produce(
            row.topic,
            key=row.key,
            value=json.dumps(row.payload, ensure_ascii=False).encode(),
            headers={"message_type": row.message_type, "message_id": str(row.message_id)},
            on_delivery=lambda err, _msg: errors.append(err) if err else None,
        )
        remaining = self.producer().flush(10)
        if remaining or errors:
            raise RuntimeError(f"Kafka не подтвердила доставку: {errors or 'timeout'}")

    def publish_rabbit(self, row: OutboxEvent) -> None:
        try:
            self.channel().basic_publish(
                exchange=COMMANDS_EXCHANGE,
                routing_key=row.topic,
                body=json.dumps(row.payload, ensure_ascii=False).encode(),
                properties=persistent_properties(str(row.message_id), row.message_type, expiration_ms=row.delay_ms),
                mandatory=True,  # нет подходящей очереди — ошибка, а не тихая потеря
            )
        except pika.exceptions.AMQPError:
            self._channel = None
            raise

    def run_once(self) -> int:
        """Один проход: до BATCH_SIZE сообщений. Возвращает, сколько отправлено."""
        sent = 0
        with SessionLocal() as db:
            # SKIP LOCKED: можно запустить несколько relay — они не возьмут одни и те же строки
            rows = db.scalars(
                select(OutboxEvent)
                .where(OutboxEvent.sent_at.is_(None))
                .order_by(OutboxEvent.id)
                .limit(BATCH_SIZE)
                .with_for_update(skip_locked=True)
            ).all()
            now = time.monotonic()
            failed_destinations = {d for d, until in self._down_until.items() if until > now}
            for row in rows:
                # Если брокер недоступен, остальные сообщения для него не шлём в этом проходе:
                # иначе нарушится порядок событий одного заказа
                if row.destination in failed_destinations:
                    continue
                try:
                    if row.destination == "kafka":
                        self.publish_kafka(row)
                    else:
                        self.publish_rabbit(row)
                    row.sent_at = datetime.now(UTC)
                    row.last_error = None
                    sent += 1
                except Exception as exc:
                    row.attempts += 1
                    row.last_error = str(exc)[:500]
                    failed_destinations.add(row.destination)
                    self._down_until[row.destination] = time.monotonic() + BROKER_BACKOFF_SECONDS
                    if row.destination == "kafka":
                        self._producer = None
                    log.warning("Не удалось отправить %s в %s: %s", row.message_type, row.destination, exc)
            db.commit()
        return sent


def main() -> None:
    setup_logging(settings.log_level)
    relay = Relay()
    log.info("Outbox relay запущен")
    while True:
        try:
            sent = relay.run_once()
        except Exception:
            log.exception("Ошибка в цикле relay")
            sent = 0
        if sent:
            log.info("Отправлено сообщений: %s", sent)
        else:
            time.sleep(settings.outbox_poll_interval_seconds)


if __name__ == "__main__":
    main()
