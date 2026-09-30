"""/health: состояние БД и брокеров (NFR-14).

Брокеры не критичны для API: заказы и оплата работают и без них (сообщения копятся
в outbox). Поэтому без брокеров статус degraded, но код ответа 200. Без БД — 503.
"""

import pika
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import settings
from app.core.db import engine

router = APIRouter(tags=["Сервис"])


def _check_db() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _check_rabbitmq() -> bool:
    try:
        params = pika.URLParameters(settings.rabbitmq_url)
        params.socket_timeout = 2
        params.blocked_connection_timeout = 2
        params.connection_attempts = 1
        pika.BlockingConnection(params).close()
        return True
    except Exception:
        return False


def _check_kafka() -> bool:
    try:
        from confluent_kafka.admin import AdminClient

        AdminClient({"bootstrap.servers": settings.kafka_bootstrap_servers, "log_level": 0}).list_topics(timeout=2)
        return True
    except Exception:
        return False


@router.get("/health", summary="Состояние сервиса и зависимостей")
def health():
    checks = {"database": _check_db(), "rabbitmq": _check_rabbitmq(), "kafka": _check_kafka()}
    if not checks["database"]:
        status_code, status = 503, "down"
    elif all(checks.values()):
        status_code, status = 200, "ok"
    else:
        status_code, status = 200, "degraded"
    return JSONResponse(
        status_code=status_code,
        content={"status": status, "checks": {k: "ok" if v else "unavailable" for k, v in checks.items()}},
    )
