from pydantic import BaseModel, Field


class CartItemAdd(BaseModel):
    product_id: int = Field(examples=[1], description="id товара из GET /products")
    quantity: int = Field(default=1, ge=1, le=10, examples=[1])


class CartItemUpdate(BaseModel):
    quantity: int = Field(ge=1, le=10)


class CartLine(BaseModel):
    product_id: int
    name: str
    quantity: int
    price_kopecks: int = Field(description="Текущая цена товара")
    line_total_kopecks: int
    available: int
    is_available: bool = Field(description="false, если товар снят с продажи или его не хватает")


class CartOut(BaseModel):
    items: list[CartLine]
    total_kopecks: int
    has_unavailable_items: bool
