from enum import StrEnum

from pydantic import BaseModel, Field


class ProductSort(StrEnum):
    newest = "newest"
    price_asc = "price_asc"
    price_desc = "price_desc"


class CategoryOut(BaseModel):
    id: int
    name: str
    parent_id: int | None
    children: list["CategoryOut"] = []


class ProductShort(BaseModel):
    id: int
    sku: str
    name: str
    category_id: int
    price_kopecks: int
    available: int = Field(description="Доступный остаток: quantity - reserved")
    in_stock: bool


class ProductOut(ProductShort):
    description: str


class ProductPage(BaseModel):
    items: list[ProductShort]
    total: int
    limit: int
    offset: int
