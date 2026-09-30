"""Индекс на categories.parent_id: у внешнего ключа не было индекса.

Нужен для поиска подкатегорий (рекурсивный фильтр каталога) и для проверки
при удалении категории, есть ли у неё дочерние.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-30
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_categories_parent_id", "categories", ["parent_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_categories_parent_id", table_name="categories")
