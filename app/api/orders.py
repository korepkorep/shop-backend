from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user
from app.models import User
from app.schemas.order import OrderOut, OrderPage, OrderStatus, PaymentOut
from app.services import orders as service
from app.services import payments
from app.services.idempotency import run_idempotent

router = APIRouter(prefix="/orders", tags=["Заказы и оплата"])

IDEMPOTENCY_HEADER = Header(
    ...,
    alias="Idempotency-Key",
    description="Уникальный ключ запроса (например, UUID). Повтор с тем же ключом не создаст дубль.",
)


@router.post("", response_model=OrderOut, status_code=201, summary="Оформить заказ из корзины (US-07)")
def create_order(
    idempotency_key: str = IDEMPOTENCY_HEADER,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    status, body = run_idempotent(
        db,
        user_id=user.id,
        endpoint="POST /orders",
        key=idempotency_key,
        body={},
        operation=lambda: service.create_order(db, user),
    )
    return JSONResponse(status_code=status, content=body)


@router.get("", response_model=OrderPage, summary="Мои заказы (US-08)")
def list_orders(
    status: OrderStatus | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return service.list_user_orders(db, user, status.value if status else None, limit, offset)


@router.get("/{order_id}", response_model=OrderOut, summary="Детали заказа с историей статусов (US-08)")
def get_order(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.serialize_order(service.get_user_order(db, user, order_id))


@router.post("/{order_id}/cancel", response_model=OrderOut, summary="Отменить неоплаченный заказ (US-09)")
def cancel_order(order_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.cancel_order(db, user, order_id)


@router.post(
    "/{order_id}/payments",
    response_model=PaymentOut,
    status_code=201,
    summary="Создать платёж и получить ссылку на оплату (US-10)",
    responses={200: {"description": "У заказа уже есть активный платёж — возвращается он (BR-06)"}},
)
def create_payment(
    order_id: int,
    idempotency_key: str = IDEMPOTENCY_HEADER,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    status, body = run_idempotent(
        db,
        user_id=user.id,
        endpoint=f"POST /orders/{order_id}/payments",
        key=idempotency_key,
        body={"order_id": order_id},
        operation=lambda: payments.create_payment(db, user, order_id, idempotency_key),
    )
    return JSONResponse(status_code=status, content=body)
