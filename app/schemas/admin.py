from datetime import datetime

from pydantic import BaseModel, Field


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    parent_id: int | None = None


class ProductCreate(BaseModel):
    category_id: int
    sku: str = Field(min_length=1, max_length=64, examples=["PH-IPHONE-15"])
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    price_kopecks: int = Field(gt=0, description="Цена в копейках: 1 000,00 ₽ = 100000")
    quantity: int = Field(default=0, ge=0, description="Начальный остаток на складе")


class ProductUpdate(BaseModel):
    category_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    price_kopecks: int | None = Field(default=None, gt=0)


class AdminProductOut(BaseModel):
    id: int
    sku: str
    name: str
    description: str
    category_id: int
    price_kopecks: int
    is_active: bool
    quantity: int
    reserved: int


class StockSet(BaseModel):
    quantity: int = Field(ge=0, description="Фактическое количество на складе")


class StockOut(BaseModel):
    product_id: int
    quantity: int
    reserved: int
    available: int


class RefundOut(BaseModel):
    order_id: int
    refund_id: int
    status: str
    amount_kopecks: int


class IncidentOut(BaseModel):
    id: int
    kind: str
    order_id: int | None
    details: dict
    created_at: datetime
