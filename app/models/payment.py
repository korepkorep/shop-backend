from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

PAYMENT_STATUSES = ("pending", "succeeded", "failed", "amount_mismatch", "refund_pending", "refunded")
REFUND_STATUSES = ("pending", "succeeded", "failed", "request_failed")
REFUND_REASONS = ("admin", "late_payment", "cancelled_order", "duplicate_payment")


class Payment(TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint(f"status IN {PAYMENT_STATUSES}", name="status"),
        CheckConstraint("amount_kopecks > 0", name="amount_positive"),
        # BR-06: у заказа не больше одного активного (pending) платежа — гарантирует БД
        Index(
            "uq_payments_one_pending_per_order",
            "order_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    # Первая линия защиты от двойного проведения: один платёж PSP = одна строка
    provider_payment_id: Mapped[str] = mapped_column(String(64), unique=True)
    amount_kopecks: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    payment_url: Mapped[str] = mapped_column(Text)

    refund: Mapped["Refund | None"] = relationship(back_populates="payment", uselist=False)


class Refund(TimestampMixin, Base):
    """Возврат — только полный (BR-05), поэтому не больше одного на платёж."""

    __tablename__ = "refunds"
    __table_args__ = (
        CheckConstraint(f"status IN {REFUND_STATUSES}", name="status"),
        CheckConstraint(f"reason IN {REFUND_REASONS}", name="reason"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.id"), unique=True)
    provider_refund_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    amount_kopecks: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    reason: Mapped[str] = mapped_column(String(32))
    last_error: Mapped[str | None] = mapped_column(Text)

    payment: Mapped[Payment] = relationship(back_populates="refund")
