from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, TimestampMixin


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    # Индекс на внешний ключ: быстрый поиск подкатегорий и проверка при удалении родителя
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), index=True)


class Product(TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("price_kopecks > 0", name="price_positive"),
        Index("ix_products_active_created", "is_active", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), index=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    # Деньги — целое число копеек, никаких float (BR-09)
    price_kopecks: Mapped[int] = mapped_column(BigInteger)
    # Товар не удаляется, а снимается с продажи: на него ссылаются заказы (BR-07)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    stock: Mapped["Stock"] = relationship(back_populates="product", uselist=False)


class Stock(Base):
    """Остаток на складе. Доступно к покупке = quantity - reserved."""

    __tablename__ = "stock"
    __table_args__ = (
        CheckConstraint("quantity >= 0", name="quantity_non_negative"),
        CheckConstraint("reserved >= 0", name="reserved_non_negative"),
        # Даже при баге в коде резерв не превысит остаток — БД не даст
        CheckConstraint("reserved <= quantity", name="reserved_le_quantity"),
    )

    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), primary_key=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    product: Mapped[Product] = relationship(back_populates="stock")

    @property
    def available(self) -> int:
        return self.quantity - self.reserved


class StockLog(CreatedAtMixin, Base):
    """Журнал ручных изменений склада: кто, когда, было, стало (US-16)."""

    __tablename__ = "stock_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    changed_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    old_quantity: Mapped[int] = mapped_column(Integer)
    new_quantity: Mapped[int] = mapped_column(Integer)
