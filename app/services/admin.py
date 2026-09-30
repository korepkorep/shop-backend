"""Администрирование: товары, склад, отгрузка, доставка (US-15, US-16, US-17)."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError, not_found
from app.models import Category, Incident, Order, Product, Stock, StockLog, User
from app.services import messaging
from app.services.orders import serialize_order
from app.services.statuses import transition


def _admin_product(product: Product, stock: Stock | None) -> dict:
    return {
        "id": product.id,
        "sku": product.sku,
        "name": product.name,
        "description": product.description,
        "category_id": product.category_id,
        "price_kopecks": product.price_kopecks,
        "is_active": product.is_active,
        "quantity": stock.quantity if stock else 0,
        "reserved": stock.reserved if stock else 0,
    }


def create_category(db: Session, name: str, parent_id: int | None) -> dict:
    if parent_id is not None and db.get(Category, parent_id) is None:
        raise not_found("Родительская категория")
    category = Category(name=name, parent_id=parent_id)
    db.add(category)
    db.commit()
    return {"id": category.id, "name": category.name, "parent_id": category.parent_id, "children": []}


def create_product(db: Session, data: dict) -> dict:
    if db.get(Category, data["category_id"]) is None:
        raise not_found("Категория")
    if db.scalar(select(Product.id).where(Product.sku == data["sku"])):
        raise AppError(409, "SKU_TAKEN", "Товар с таким SKU уже существует", {"sku": data["sku"]})
    quantity = data.pop("quantity", 0)
    product = Product(**data, is_active=True)
    product.stock = Stock(quantity=quantity, reserved=0)
    db.add(product)
    db.commit()
    return _admin_product(product, product.stock)


def update_product(db: Session, product_id: int, changes: dict) -> dict:
    product = db.get(Product, product_id)
    if product is None:
        raise not_found("Товар")
    if "category_id" in changes and db.get(Category, changes["category_id"]) is None:
        raise not_found("Категория")
    # Изменение цены не трогает оформленные заказы: там своя копия цены (BR-02)
    for field, value in changes.items():
        setattr(product, field, value)
    db.commit()
    return _admin_product(product, product.stock)


def deactivate_product(db: Session, product_id: int) -> None:
    """Физически не удаляем: на товар ссылаются заказы (BR-07)."""
    product = db.get(Product, product_id)
    if product is None:
        raise not_found("Товар")
    product.is_active = False
    db.commit()


def set_stock(db: Session, admin: User, product_id: int, quantity: int) -> dict:
    stock = db.scalar(
        select(Stock).where(Stock.product_id == product_id).with_for_update().execution_options(populate_existing=True)
    )
    if stock is None:
        raise not_found("Товар")
    if quantity < stock.reserved:
        raise AppError(
            409,
            "STOCK_BELOW_RESERVED",
            "Нельзя установить остаток меньше текущего резерва",
            {"quantity": quantity, "reserved": stock.reserved},
        )
    db.add(StockLog(product_id=product_id, changed_by=admin.id, old_quantity=stock.quantity, new_quantity=quantity))
    stock.quantity = quantity
    db.commit()
    return {
        "product_id": product_id,
        "quantity": stock.quantity,
        "reserved": stock.reserved,
        "available": stock.available,
    }


def list_products(db: Session, limit: int, offset: int) -> list[dict]:
    rows = db.execute(select(Product, Stock).outerjoin(Stock).order_by(Product.id).limit(limit).offset(offset)).all()
    return [_admin_product(p, s) for p, s in rows]


def list_orders(db: Session, status: str | None, limit: int, offset: int) -> dict:
    query = select(Order)
    if status:
        query = query.where(Order.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    orders = db.scalars(query.order_by(Order.id.desc()).limit(limit).offset(offset)).all()
    return {"items": [serialize_order(o) for o in orders], "total": total, "limit": limit, "offset": offset}


def _admin_transition(db: Session, order_id: int, target: str, event_type: str, template: str | None) -> dict:
    order = db.scalar(
        select(Order).where(Order.id == order_id).with_for_update().execution_options(populate_existing=True)
    )
    if order is None:
        raise not_found("Заказ")
    transition(db, order, target, actor="admin", reason=f"marked_{target}")
    messaging.publish_order_event(db, order, event_type)
    if template:
        messaging.send_notification(db, db.get(User, order.user_id), template, order)
    db.commit()
    db.refresh(order)
    return serialize_order(order)


def ship_order(db: Session, order_id: int) -> dict:
    return _admin_transition(db, order_id, "shipped", "OrderShipped", "order_shipped")


def deliver_order(db: Session, order_id: int) -> dict:
    return _admin_transition(db, order_id, "delivered", "OrderDelivered", None)


def list_incidents(db: Session, kind: str | None, limit: int) -> list[dict]:
    query = select(Incident)
    if kind:
        query = query.where(Incident.kind == kind)
    rows = db.scalars(query.order_by(Incident.id.desc()).limit(limit)).all()
    return [
        {"id": i.id, "kind": i.kind, "order_id": i.order_id, "details": i.details, "created_at": i.created_at}
        for i in rows
    ]
