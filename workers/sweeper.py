"""Страховка для истечения заказов: раз в N минут ищет просроченные неоплаченные заказы
и переводит их в expired. Нужна на случай, если команда из RabbitMQ потерялась.

Запуск: python -m workers.sweeper
"""

import logging
import time

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.logging import setup_logging
from app.services.orders import expire_order, find_overdue_order_ids

log = logging.getLogger("sweeper")


def sweep_once() -> int:
    with SessionLocal() as db:
        order_ids = find_overdue_order_ids(db)
    expired = 0
    for order_id in order_ids:
        with SessionLocal() as db:
            if expire_order(db, order_id, source="sweeper"):
                expired += 1
    return expired


def main() -> None:
    setup_logging(settings.log_level)
    log.info("Sweeper запущен, интервал %s с", settings.sweep_interval_seconds)
    while True:
        try:
            count = sweep_once()
            if count:
                log.warning("Sweeper перевёл в expired заказов: %s (команды из RabbitMQ не сработали)", count)
        except Exception:
            log.exception("Ошибка sweeper")
        time.sleep(settings.sweep_interval_seconds)


if __name__ == "__main__":
    main()
