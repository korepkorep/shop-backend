"""Воркер истечения резервов (US-13): читает команды ExpireReservation из очереди reservations.expire.

Команда попадает сюда не сразу: сначала она лежит в очереди reservations.delay
без консьюмеров, а когда истекает её TTL (15 минут), RabbitMQ через dead letter
exchange перекладывает её в reservations.expire.

Запуск: python -m workers.reservations
"""

import json
import logging

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.logging import setup_logging
from app.services.orders import expire_order
from workers.brokers import Q_RESERVATIONS_EXPIRE, run_rabbit_consumer

log = logging.getLogger("reservations")


def on_message(channel, method, properties, body: bytes) -> None:
    order_id = json.loads(body)["payload"]["order_id"]
    try:
        with SessionLocal() as db:
            expired = expire_order(db, order_id, source="rabbitmq_ttl")
        if not expired:
            log.info("Заказ уже оплачен или отменён — резерв не трогаем", extra={"order_id": order_id})
    except Exception:
        # Не блокируем очередь: если не вышло, заказ подберёт sweeper
        log.exception("Не удалось обработать истечение заказа", extra={"order_id": order_id})
    channel.basic_ack(delivery_tag=method.delivery_tag)


def main() -> None:
    setup_logging(settings.log_level)
    run_rabbit_consumer(Q_RESERVATIONS_EXPIRE, on_message)


if __name__ == "__main__":
    main()
