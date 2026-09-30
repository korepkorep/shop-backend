"""Начальная схема БД: магазин (public), витрина (analytics), журнал событий (audit).

Revision ID: 0001
Revises:
Create Date: 2026-09-30
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Отдельные схемы для консьюмеров Kafka: магазин в них не пишет
    op.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    op.execute("CREATE SCHEMA IF NOT EXISTS audit")

    op.create_table(
        "orders_daily",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("created", sa.Integer(), nullable=False),
        sa.Column("paid", sa.Integer(), nullable=False),
        sa.Column("expired", sa.Integer(), nullable=False),
        sa.Column("cancelled", sa.Integer(), nullable=False),
        sa.Column("refunded", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("day", name=op.f("pk_orders_daily")),
        schema="analytics",
    )
    op.create_table(
        "processed_events",
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_processed_events")),
        schema="analytics",
    )
    op.create_table(
        "sales_daily",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("product_name", sa.String(length=200), nullable=False),
        sa.Column("orders_count", sa.Integer(), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("revenue_kopecks", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("day", "product_id", name=op.f("pk_sales_daily")),
        schema="analytics",
    )
    op.create_table(
        "event_log",
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_event_log")),
        schema="audit",
    )
    op.create_index(op.f("ix_audit_event_log_event_type"), "event_log", ["event_type"], unique=False, schema="audit")
    op.create_index(op.f("ix_audit_event_log_order_id"), "event_log", ["order_id"], unique=False, schema="audit")
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["parent_id"], ["categories.id"], name=op.f("fk_categories_parent_id_categories")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_categories")),
    )
    op.create_table(
        "incidents",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incidents")),
    )
    op.create_index(op.f("ix_incidents_kind"), "incidents", ["kind"], unique=False)
    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("command_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("template", sa.String(length=64), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=True),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
        sa.UniqueConstraint("command_id", name=op.f("uq_notifications_command_id")),
    )
    op.create_index(op.f("ix_notifications_order_id"), "notifications", ["order_id"], unique=False)
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.UUID(), nullable=False),
        sa.Column("destination", sa.String(length=16), nullable=False),
        sa.Column("topic", sa.String(length=100), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=True),
        sa.Column("message_type", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("delay_ms", sa.Integer(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("destination IN ('kafka', 'rabbitmq')", name=op.f("ck_outbox_events_destination")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_events")),
        sa.UniqueConstraint("message_id", name=op.f("uq_outbox_events_message_id")),
    )
    op.create_index(
        "ix_outbox_unsent", "outbox_events", ["id"], unique=False, postgresql_where=sa.text("sent_at IS NULL")
    )
    op.create_table(
        "processed_messages",
        sa.Column("consumer", sa.String(length=64), nullable=False),
        sa.Column("message_id", sa.UUID(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("consumer", "message_id", name=op.f("pk_processed_messages")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("role IN ('customer', 'admin')", name=op.f("ck_users_role")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "carts",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_carts_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_carts")),
        sa.UniqueConstraint("user_id", name=op.f("uq_carts_user_id")),
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("endpoint", sa.String(length=128), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_idempotency_keys_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_keys")),
        sa.UniqueConstraint("user_id", "endpoint", "key", name="uq_idempotency_keys_user_endpoint_key"),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("total_kopecks", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('created', 'paid', 'shipped', 'delivered', 'cancelled', 'expired', 'refunded')",
            name=op.f("ck_orders_status"),
        ),
        sa.CheckConstraint("total_kopecks > 0", name=op.f("ck_orders_total_positive")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_orders_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
    )
    op.create_index("ix_orders_status_expires", "orders", ["status", "expires_at"], unique=False)
    op.create_index("ix_orders_user_created", "orders", ["user_id", "created_at"], unique=False)
    op.create_table(
        "products",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("price_kopecks", sa.BigInteger(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("price_kopecks > 0", name=op.f("ck_products_price_positive")),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], name=op.f("fk_products_category_id_categories")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
        sa.UniqueConstraint("sku", name=op.f("uq_products_sku")),
    )
    op.create_index("ix_products_active_created", "products", ["is_active", "created_at"], unique=False)
    op.create_index(op.f("ix_products_category_id"), "products", ["category_id"], unique=False)
    op.create_table(
        "cart_items",
        sa.Column("cart_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("quantity BETWEEN 1 AND 10", name=op.f("ck_cart_items_quantity_range")),
        sa.ForeignKeyConstraint(
            ["cart_id"], ["carts.id"], name=op.f("fk_cart_items_cart_id_carts"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name=op.f("fk_cart_items_product_id_products")),
        sa.PrimaryKeyConstraint("cart_id", "product_id", name=op.f("pk_cart_items")),
    )
    op.create_table(
        "order_items",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("product_name", sa.String(length=200), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("price_kopecks", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("price_kopecks > 0", name=op.f("ck_order_items_price_positive")),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_order_items_quantity_positive")),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_order_items_order_id_orders"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name=op.f("fk_order_items_product_id_products")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_items")),
        sa.UniqueConstraint("order_id", "product_id", name=op.f("uq_order_items_order_id")),
    )
    op.create_table(
        "order_status_history",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("from_status", sa.String(length=16), nullable=True),
        sa.Column("to_status", sa.String(length=16), nullable=False),
        sa.Column("actor", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name=op.f("fk_order_status_history_order_id_orders"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_status_history")),
    )
    op.create_index(op.f("ix_order_status_history_order_id"), "order_status_history", ["order_id"], unique=False)
    op.create_table(
        "payments",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("provider_payment_id", sa.String(length=64), nullable=False),
        sa.Column("amount_kopecks", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("payment_url", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed', 'amount_mismatch', 'refund_pending', 'refunded')",
            name=op.f("ck_payments_status"),
        ),
        sa.CheckConstraint("amount_kopecks > 0", name=op.f("ck_payments_amount_positive")),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], name=op.f("fk_payments_order_id_orders")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payments")),
        sa.UniqueConstraint("provider_payment_id", name=op.f("uq_payments_provider_payment_id")),
    )
    op.create_index(op.f("ix_payments_order_id"), "payments", ["order_id"], unique=False)
    op.create_index(
        "uq_payments_one_pending_per_order",
        "payments",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "stock",
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("reserved", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("quantity >= 0", name=op.f("ck_stock_quantity_non_negative")),
        sa.CheckConstraint("reserved <= quantity", name=op.f("ck_stock_reserved_le_quantity")),
        sa.CheckConstraint("reserved >= 0", name=op.f("ck_stock_reserved_non_negative")),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name=op.f("fk_stock_product_id_products")),
        sa.PrimaryKeyConstraint("product_id", name=op.f("pk_stock")),
    )
    op.create_table(
        "stock_log",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("changed_by", sa.BigInteger(), nullable=False),
        sa.Column("old_quantity", sa.Integer(), nullable=False),
        sa.Column("new_quantity", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["changed_by"], ["users.id"], name=op.f("fk_stock_log_changed_by_users")),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name=op.f("fk_stock_log_product_id_products")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_log")),
    )
    op.create_index(op.f("ix_stock_log_product_id"), "stock_log", ["product_id"], unique=False)
    op.create_table(
        "refunds",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("payment_id", sa.BigInteger(), nullable=False),
        sa.Column("provider_refund_id", sa.String(length=64), nullable=True),
        sa.Column("amount_kopecks", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "reason IN ('admin', 'late_payment', 'cancelled_order', 'duplicate_payment')",
            name=op.f("ck_refunds_reason"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed', 'request_failed')", name=op.f("ck_refunds_status")
        ),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], name=op.f("fk_refunds_payment_id_payments")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refunds")),
        sa.UniqueConstraint("payment_id", name=op.f("uq_refunds_payment_id")),
        sa.UniqueConstraint("provider_refund_id", name=op.f("uq_refunds_provider_refund_id")),
    )


def downgrade() -> None:
    op.drop_table("refunds")
    op.drop_index(op.f("ix_stock_log_product_id"), table_name="stock_log")
    op.drop_table("stock_log")
    op.drop_table("stock")
    op.drop_index(
        "uq_payments_one_pending_per_order", table_name="payments", postgresql_where=sa.text("status = 'pending'")
    )
    op.drop_index(op.f("ix_payments_order_id"), table_name="payments")
    op.drop_table("payments")
    op.drop_index(op.f("ix_order_status_history_order_id"), table_name="order_status_history")
    op.drop_table("order_status_history")
    op.drop_table("order_items")
    op.drop_table("cart_items")
    op.drop_index(op.f("ix_products_category_id"), table_name="products")
    op.drop_index("ix_products_active_created", table_name="products")
    op.drop_table("products")
    op.drop_index("ix_orders_user_created", table_name="orders")
    op.drop_index("ix_orders_status_expires", table_name="orders")
    op.drop_table("orders")
    op.drop_table("idempotency_keys")
    op.drop_table("carts")
    op.drop_table("users")
    op.drop_table("processed_messages")
    op.drop_index("ix_outbox_unsent", table_name="outbox_events", postgresql_where=sa.text("sent_at IS NULL"))
    op.drop_table("outbox_events")
    op.drop_index(op.f("ix_notifications_order_id"), table_name="notifications")
    op.drop_table("notifications")
    op.drop_index(op.f("ix_incidents_kind"), table_name="incidents")
    op.drop_table("incidents")
    op.drop_table("categories")
    op.drop_index(op.f("ix_audit_event_log_order_id"), table_name="event_log", schema="audit")
    op.drop_index(op.f("ix_audit_event_log_event_type"), table_name="event_log", schema="audit")
    op.drop_table("event_log", schema="audit")
    op.drop_table("sales_daily", schema="analytics")
    op.drop_table("processed_events", schema="analytics")
    op.drop_table("orders_daily", schema="analytics")
    op.execute("DROP SCHEMA IF EXISTS analytics")
    op.execute("DROP SCHEMA IF EXISTS audit")
