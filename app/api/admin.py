from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import require_admin
from app.models import User
from app.schemas.admin import (
    AdminProductOut,
    CategoryCreate,
    IncidentOut,
    ProductCreate,
    ProductUpdate,
    RefundOut,
    StockOut,
    StockSet,
)
from app.schemas.catalog import CategoryOut
from app.schemas.order import OrderOut, OrderStatus
from app.services import admin as service
from app.services import payments

# require_admin на весь роутер: без роли admin → 403 (US-15 AC 4)
router = APIRouter(prefix="/admin", tags=["Администрирование"], dependencies=[Depends(require_admin)])


@router.post("/categories", response_model=CategoryOut, status_code=201, summary="Создать категорию")
def create_category(body: CategoryCreate, db: Session = Depends(get_db)):
    return service.create_category(db, body.name, body.parent_id)


@router.get("/products", response_model=list[AdminProductOut], summary="Все товары, включая снятые с продажи")
def list_products(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    return service.list_products(db, limit, offset)


@router.post("/products", response_model=AdminProductOut, status_code=201, summary="Создать товар (US-15)")
def create_product(body: ProductCreate, db: Session = Depends(get_db)):
    return service.create_product(db, body.model_dump())


@router.patch("/products/{product_id}", response_model=AdminProductOut, summary="Изменить товар (US-15)")
def update_product(product_id: int, body: ProductUpdate, db: Session = Depends(get_db)):
    return service.update_product(db, product_id, body.model_dump(exclude_unset=True))


@router.delete("/products/{product_id}", status_code=204, summary="Снять товар с продажи (не удаляет физически)")
def deactivate_product(product_id: int, db: Session = Depends(get_db)):
    service.deactivate_product(db, product_id)
    return Response(status_code=204)


@router.put(
    "/stock/{product_id}",
    response_model=StockOut,
    summary="Задать фактический остаток (US-16)",
    description="PUT, потому что задаём абсолютное значение: повтор запроса даёт тот же результат.",
)
def set_stock(product_id: int, body: StockSet, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return service.set_stock(db, admin, product_id, body.quantity)


@router.get("/orders", summary="Все заказы")
def list_orders(
    status: OrderStatus | None = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return service.list_orders(db, status.value if status else None, limit, offset)


@router.post("/orders/{order_id}/ship", response_model=OrderOut, summary="Отгрузить заказ (US-17)")
def ship(order_id: int, db: Session = Depends(get_db)):
    return service.ship_order(db, order_id)


@router.post("/orders/{order_id}/deliver", response_model=OrderOut, summary="Отметить доставку (US-17)")
def deliver(order_id: int, db: Session = Depends(get_db)):
    return service.deliver_order(db, order_id)


@router.post(
    "/orders/{order_id}/refund",
    response_model=RefundOut,
    status_code=202,
    summary="Полный возврат оплаченного заказа (US-18)",
    description="202: возврат принят в обработку. Статус заказа сменится, когда PSP подтвердит возврат вебхуком.",
)
def refund(order_id: int, db: Session = Depends(get_db)):
    return payments.admin_refund(db, order_id)


@router.get("/incidents", response_model=list[IncidentOut], summary="Журнал инцидентов для ручного разбора")
def incidents(kind: str | None = None, limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    return service.list_incidents(db, kind, limit)
