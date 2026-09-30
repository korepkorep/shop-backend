from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.schemas.catalog import CategoryOut, ProductOut, ProductPage, ProductSort
from app.services import catalog as service

router = APIRouter(tags=["Каталог"])


@router.get("/categories", response_model=list[CategoryOut], summary="Дерево категорий")
def categories(db: Session = Depends(get_db)):
    return service.category_tree(db)


@router.get("/products", response_model=ProductPage, summary="Список товаров с фильтрами (US-03)")
def products(
    category_id: int | None = Query(None, description="Категория вместе со всеми подкатегориями"),
    price_min: int | None = Query(None, ge=0, description="Цена от, в копейках"),
    price_max: int | None = Query(None, ge=0, description="Цена до, в копейках"),
    q: str | None = Query(None, max_length=100, description="Поиск по названию без учёта регистра"),
    sort: ProductSort = ProductSort.newest,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return service.list_products(
        db,
        category_id=category_id,
        price_min=price_min,
        price_max=price_max,
        q=q,
        sort=sort,
        limit=limit,
        offset=offset,
    )


@router.get("/products/{product_id}", response_model=ProductOut, summary="Карточка товара (US-04)")
def product(product_id: int, db: Session = Depends(get_db)):
    return service.get_product(db, product_id)
