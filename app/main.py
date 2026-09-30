"""Точка входа Shop API. Swagger: http://localhost:8000/docs"""

import logging
import time
import uuid

from fastapi import FastAPI, Request

from app.api import admin, auth, cart, catalog, health, orders, webhooks
from app.core.config import settings
from app.core.errors import register_error_handlers
from app.core.logging import request_id_var, setup_logging

setup_logging(settings.log_level)
log = logging.getLogger("app.access")

app = FastAPI(
    title="ShopCore API",
    version="1.0.0",
    description=(
        "Бэкенд интернет-магазина: каталог, корзина, заказы с резервированием, "
        "оплата через PSP, события в Kafka и команды в RabbitMQ.\n\n"
        "**Как пройти сценарий покупки:** `POST /auth/login` (buyer@example.com / Buyer12345) → "
        "кнопка Authorize → `POST /cart/items` → `POST /orders` → `POST /orders/{id}/payments` → "
        "открыть `payment_url` и нажать «Оплатить» → `GET /orders/{id}`.\n\n"
        "Все суммы — в копейках. Документация: `docs/` в репозитории."
    ),
)
register_error_handlers(app)

API_PREFIX = "/api/v1"
for module in (auth, catalog, cart, orders, webhooks, admin):
    app.include_router(module.router, prefix=API_PREFIX)
app.include_router(health.router)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """request_id в каждом логе и в заголовке ответа — чтобы найти все записи одного запроса."""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        log.info("%s %s -> %s (%s ms)", request.method, request.url.path, response.status_code, duration_ms)
        return response
    finally:
        request_id_var.reset(token)
