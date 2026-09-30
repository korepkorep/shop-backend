"""Идемпотентность запросов по заголовку Idempotency-Key (US-07 AC 8, US-10 AC 4).

Как работает:
1. В той же транзакции, что и бизнес-операция, вставляем строку с ключом.
2. Если такой ключ уже есть — это повтор. Сравниваем хеш тела запроса:
   совпал — отдаём сохранённый ответ, не совпал — ошибка IDEMPOTENCY_KEY_REUSED.
3. Если операция упала с ошибкой, транзакция откатывается вместе с ключом,
   и клиент может повторить запрос с тем же ключом.

Два одновременных запроса с одним ключом: второй INSERT ждёт, пока первая
транзакция завершится (уникальный индекс), и потом видит готовый ответ.
"""

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from fastapi.encoders import jsonable_encoder
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.models import IdempotencyKey


def request_hash(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def run_idempotent(
    db: Session,
    *,
    user_id: int,
    endpoint: str,
    key: str,
    body: dict,
    operation: Callable[[], tuple[int, object]],
) -> tuple[int, object]:
    """Выполняет operation() один раз на ключ. operation возвращает (http_status, ответ)."""
    if not key or len(key) > 128:
        raise AppError(422, "VALIDATION_ERROR", "Заголовок Idempotency-Key обязателен (до 128 символов)")

    # Ключи старше 24 часов считаем забытыми (US-07 AC 8)
    cutoff = datetime.now(UTC) - timedelta(hours=settings.idempotency_key_ttl_hours)
    db.execute(
        delete(IdempotencyKey).where(
            IdempotencyKey.user_id == user_id,
            IdempotencyKey.endpoint == endpoint,
            IdempotencyKey.key == key,
            IdempotencyKey.created_at < cutoff,
        )
    )

    body_hash = request_hash(body)
    inserted_id = db.execute(
        insert(IdempotencyKey)
        .values(user_id=user_id, endpoint=endpoint, key=key, request_hash=body_hash)
        .on_conflict_do_nothing(constraint="uq_idempotency_keys_user_endpoint_key")
        .returning(IdempotencyKey.id)
    ).scalar_one_or_none()

    if inserted_id is None:
        existing = db.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.user_id == user_id, IdempotencyKey.endpoint == endpoint, IdempotencyKey.key == key
            )
        ).scalar_one()
        db.rollback()
        if existing.request_hash != body_hash:
            raise AppError(
                422,
                "IDEMPOTENCY_KEY_REUSED",
                "Этот Idempotency-Key уже использован с другими данными запроса",
            )
        return existing.response_status, existing.response_body

    status, response = operation()
    row = db.get(IdempotencyKey, inserted_id)
    row.response_status = status
    row.response_body = jsonable_encoder(response)
    db.commit()
    return status, row.response_body
