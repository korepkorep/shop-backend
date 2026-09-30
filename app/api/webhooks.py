import json

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import AppError
from app.services import payments

router = APIRouter(prefix="/webhooks", tags=["Вебхуки"])


async def raw_body(request: Request) -> bytes:
    """Подпись считается от «сырого» тела, до парсинга JSON."""
    return await request.body()


@router.post(
    "/psp",
    summary="Уведомление от платёжного провайдера (US-11, US-12, US-18)",
    description=(
        "Вызывает PSP, а не покупатель. Подпись: HMAC-SHA256 от `<X-PSP-Timestamp>.<тело>`. "
        "На дубль и на неизвестный платёж отвечаем 200, чтобы PSP перестал повторять."
    ),
)
def psp_webhook(
    raw: bytes = Depends(raw_body),
    x_psp_timestamp: str | None = Header(None),
    x_psp_signature: str | None = Header(None),
    db: Session = Depends(get_db),
):
    payments.verify_signature(raw, x_psp_timestamp, x_psp_signature)
    try:
        event = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AppError(400, "BAD_REQUEST", "Тело вебхука — не JSON") from exc
    return payments.handle_webhook(db, event)
