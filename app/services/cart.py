"""Корзина (US-05, US-06). Корзина не резервирует товар: резерв — только при оформлении заказа."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError, not_found
from app.models import Cart, CartItem, Product, Stock


def get_or_create_cart(db: Session, user_id: int) -> Cart:
    cart = db.scalar(select(Cart).where(Cart.user_id == user_id))
    if cart is None:
        cart = Cart(user_id=user_id)
        db.add(cart)
        db.flush()
    return cart


def cart_view(db: Session, user_id: int) -> dict:
    cart = db.scalar(select(Cart).where(Cart.user_id == user_id))
    if cart is None or not cart.items:
        return {"items": [], "total_kopecks": 0, "has_unavailable_items": False}

    product_ids = [i.product_id for i in cart.items]
    rows = db.execute(
        select(Product, Stock).outerjoin(Stock, Stock.product_id == Product.id).where(Product.id.in_(product_ids))
    ).all()
    products = {p.id: (p, s) for p, s in rows}

    lines = []
    for item in cart.items:
        product, stock = products[item.product_id]
        available = stock.available if stock else 0
        # US-06 AC 3: позиция помечается «недоступна», но не удаляется
        is_available = product.is_active and available >= item.quantity
        lines.append(
            {
                "product_id": product.id,
                "name": product.name,
                "quantity": item.quantity,
                "price_kopecks": product.price_kopecks,
                "line_total_kopecks": product.price_kopecks * item.quantity,
                "available": available,
                "is_available": is_available,
            }
        )
    return {
        "items": lines,
        "total_kopecks": sum(line["line_total_kopecks"] for line in lines),
        "has_unavailable_items": not all(line["is_available"] for line in lines),
    }


def add_item(db: Session, user_id: int, product_id: int, quantity: int) -> dict:
    product = db.get(Product, product_id)
    if product is None:
        raise not_found("Товар")
    if not product.is_active:
        raise AppError(409, "PRODUCT_INACTIVE", "Товар снят с продажи", {"product_id": product_id})

    cart = get_or_create_cart(db, user_id)
    item = db.get(CartItem, (cart.id, product_id))
    new_quantity = (item.quantity if item else 0) + quantity
    if new_quantity > settings.cart_max_quantity:
        raise AppError(
            422,
            "CART_QUANTITY_LIMIT",
            f"Одного товара в корзине может быть не больше {settings.cart_max_quantity} шт.",
            {"product_id": product_id, "requested": new_quantity},
        )
    stock = db.get(Stock, product_id)
    available = stock.available if stock else 0
    if new_quantity > available:
        raise AppError(
            409,
            "OUT_OF_STOCK",
            "Недостаточно товара на складе",
            [{"product_id": product_id, "requested": new_quantity, "available": available}],
        )

    if item:
        item.quantity = new_quantity
    else:
        db.add(CartItem(cart_id=cart.id, product_id=product_id, quantity=new_quantity))
    db.commit()
    db.expire_all()
    return cart_view(db, user_id)


def update_item(db: Session, user_id: int, product_id: int, quantity: int) -> dict:
    cart = db.scalar(select(Cart).where(Cart.user_id == user_id))
    item = db.get(CartItem, (cart.id, product_id)) if cart else None
    if item is None:
        raise not_found("Товар в корзине")
    item.quantity = quantity
    db.commit()
    db.expire_all()
    return cart_view(db, user_id)


def remove_item(db: Session, user_id: int, product_id: int) -> None:
    """Повторное удаление не ошибка (US-06 AC 2) — DELETE идемпотентен."""
    cart = db.scalar(select(Cart).where(Cart.user_id == user_id))
    if cart is None:
        return
    item = db.get(CartItem, (cart.id, product_id))
    if item is not None:
        db.delete(item)
        db.commit()
