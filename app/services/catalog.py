"""Каталог товаров (US-03, US-04)."""

from sqlalchemy import func, literal, select
from sqlalchemy.orm import Session

from app.core.errors import not_found
from app.models import Category, Product, Stock
from app.schemas.catalog import ProductSort


def category_tree(db: Session) -> list[dict]:
    categories = db.scalars(select(Category).order_by(Category.name)).all()
    nodes = {c.id: {"id": c.id, "name": c.name, "parent_id": c.parent_id, "children": []} for c in categories}
    roots = []
    for node in nodes.values():
        parent = nodes.get(node["parent_id"])
        (parent["children"] if parent else roots).append(node)
    return roots


def _category_with_descendants(category_id: int):
    """Рекурсивный CTE: категория и все её подкатегории на любой глубине."""
    tree = select(Category.id).where(Category.id == category_id).cte("tree", recursive=True)
    tree = tree.union_all(select(Category.id).where(Category.parent_id == tree.c.id))
    return select(tree.c.id)


def _product_row(product: Product, stock: Stock | None, with_description: bool = False) -> dict:
    available = stock.available if stock else 0
    row = {
        "id": product.id,
        "sku": product.sku,
        "name": product.name,
        "category_id": product.category_id,
        "price_kopecks": product.price_kopecks,
        "available": available,
        "in_stock": available > 0,
    }
    if with_description:
        row["description"] = product.description
    return row


def list_products(
    db: Session,
    *,
    category_id: int | None,
    price_min: int | None,
    price_max: int | None,
    q: str | None,
    sort: ProductSort,
    limit: int,
    offset: int,
) -> dict:
    query = select(Product, Stock).outerjoin(Stock, Stock.product_id == Product.id).where(Product.is_active.is_(True))
    if category_id is not None:
        query = query.where(Product.category_id.in_(_category_with_descendants(category_id)))
    if price_min is not None:
        query = query.where(Product.price_kopecks >= price_min)
    if price_max is not None:
        query = query.where(Product.price_kopecks <= price_max)
    if q:
        query = query.where(Product.name.ilike(literal("%") + q + literal("%")))

    total = db.scalar(select(func.count()).select_from(query.subquery()))

    order = {
        ProductSort.newest: (Product.created_at.desc(), Product.id.desc()),
        ProductSort.price_asc: (Product.price_kopecks.asc(), Product.id.asc()),
        ProductSort.price_desc: (Product.price_kopecks.desc(), Product.id.asc()),
    }[sort]
    rows = db.execute(query.order_by(*order).limit(limit).offset(offset)).all()
    return {
        "items": [_product_row(p, s) for p, s in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def get_product(db: Session, product_id: int) -> dict:
    row = db.execute(
        select(Product, Stock)
        .outerjoin(Stock, Stock.product_id == Product.id)
        .where(Product.id == product_id, Product.is_active.is_(True))
    ).first()
    if row is None:
        raise not_found("Товар")
    return _product_row(row[0], row[1], with_description=True)
