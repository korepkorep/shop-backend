"""Консьюмер Kafka «analytics»: строит витрину продаж из событий заказов (US-19).

Витрина лежит в схеме analytics и обновляется только отсюда. Магазин в неё не пишет,
а аналитики не ходят в рабочие таблицы — нагрузка разделена.

Идемпотентность: event_id записывается в analytics.processed_events в той же транзакции,
что и обновление витрины. Дубль события не пройдёт по первичному ключу — витрина не исказится.

Запуск: python -m workers.analytics
"""

import logging
import uuid
from datetime import date, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.logging import setup_logging
from app.models import AnalyticsProcessedEvent, OrdersDaily, SalesDaily
from workers.brokers import run_kafka_consumer

log = logging.getLogger("analytics")
GROUP_ID = "analytics"

FUNNEL_COLUMN = {
    "OrderCreated": "created",
    "OrderPaid": "paid",
    "OrderExpired": "expired",
    "OrderCancelled": "cancelled",
    "OrderRefunded": "refunded",
}


def _bump_funnel(db: Session, day: date, column: str) -> None:
    values = {"day": day, "created": 0, "paid": 0, "expired": 0, "cancelled": 0, "refunded": 0, column: 1}
    stmt = insert(OrdersDaily).values(**values)
    db.execute(
        stmt.on_conflict_do_update(index_elements=[OrdersDaily.day], set_={column: getattr(OrdersDaily, column) + 1})
    )


def _add_sales(db: Session, day: date, items: list[dict], sign: int) -> None:
    for item in items:
        revenue = item["price_kopecks"] * item["quantity"]
        stmt = insert(SalesDaily).values(
            day=day,
            product_id=item["product_id"],
            product_name=item["product_name"],
            orders_count=sign,
            units=sign * item["quantity"],
            revenue_kopecks=sign * revenue,
        )
        db.execute(
            stmt.on_conflict_do_update(
                index_elements=[SalesDaily.day, SalesDaily.product_id],
                set_={
                    "orders_count": SalesDaily.orders_count + sign,
                    "units": SalesDaily.units + sign * item["quantity"],
                    "revenue_kopecks": SalesDaily.revenue_kopecks + sign * revenue,
                    "product_name": item["product_name"],
                },
            )
        )


def handle_event(db: Session, event: dict) -> bool:
    """Возвращает False, если событие уже было обработано (дубль)."""
    inserted = db.execute(
        insert(AnalyticsProcessedEvent)
        .values(event_id=uuid.UUID(event["event_id"]))
        .on_conflict_do_nothing()
        .returning(AnalyticsProcessedEvent.event_id)
    ).scalar_one_or_none()
    if inserted is None:
        db.rollback()
        return False

    day = datetime.fromisoformat(event["occurred_at"]).date()
    event_type = event["event_type"]
    if event_type in FUNNEL_COLUMN:
        _bump_funnel(db, day, FUNNEL_COLUMN[event_type])
    if event_type == "OrderPaid":
        _add_sales(db, day, event["payload"]["items"], sign=+1)
    elif event_type == "OrderRefunded":
        # Возврат уменьшает продажи дня, когда случился возврат
        _add_sales(db, day, event["payload"]["items"], sign=-1)
    db.commit()
    return True


def process(event: dict) -> None:
    with SessionLocal() as db:
        if handle_event(db, event):
            log.info("Учтено событие %s", event["event_type"], extra={"order_id": event["order_id"]})


def main() -> None:
    setup_logging(settings.log_level)
    run_kafka_consumer(GROUP_ID, process)


if __name__ == "__main__":
    main()
