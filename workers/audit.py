"""Консьюмер Kafka «audit»: бессрочный журнал всех событий заказов (схема audit).

Kafka хранит события 7 дней, а журнал — всегда. Отдельная consumer group:
читает тот же топик, что и analytics, но независимо, со своими offset.

Запуск: python -m workers.audit
"""

import logging
import uuid
from datetime import datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.logging import setup_logging
from app.models import AuditEvent
from workers.brokers import run_kafka_consumer

log = logging.getLogger("audit")
GROUP_ID = "audit"


def handle_event(db: Session, event: dict) -> bool:
    # Первичный ключ по event_id: дубль просто не вставится
    inserted = db.execute(
        insert(AuditEvent)
        .values(
            event_id=uuid.UUID(event["event_id"]),
            event_type=event["event_type"],
            order_id=event["order_id"],
            occurred_at=datetime.fromisoformat(event["occurred_at"]),
            payload=event,
        )
        .on_conflict_do_nothing()
        .returning(AuditEvent.event_id)
    ).scalar_one_or_none()
    db.commit()
    return inserted is not None


def process(event: dict) -> None:
    with SessionLocal() as db:
        if handle_event(db, event):
            log.info("В журнал: %s", event["event_type"], extra={"order_id": event["order_id"]})


def main() -> None:
    setup_logging(settings.log_level)
    run_kafka_consumer(GROUP_ID, process)


if __name__ == "__main__":
    main()
