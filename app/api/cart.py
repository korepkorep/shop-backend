from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user
from app.models import User
from app.schemas.cart import CartItemAdd, CartItemUpdate, CartOut
from app.services import cart as service

router = APIRouter(prefix="/cart", tags=["Корзина"])


@router.get("", response_model=CartOut, summary="Текущая корзина с актуальными ценами")
def get_cart(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.cart_view(db, user.id)


@router.post("/items", response_model=CartOut, status_code=201, summary="Добавить товар (US-05)")
def add_item(body: CartItemAdd, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return service.add_item(db, user.id, body.product_id, body.quantity)


@router.patch("/items/{product_id}", response_model=CartOut, summary="Изменить количество (US-06)")
def update_item(
    product_id: int, body: CartItemUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return service.update_item(db, user.id, product_id, body.quantity)


@router.delete("/items/{product_id}", status_code=204, summary="Удалить позицию (идемпотентно)")
def remove_item(product_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    service.remove_item(db, user.id, product_id)
    return Response(status_code=204)
