"""Общие фикстуры. Тесты работают с настоящим PostgreSQL (БД shop_test),
потому что главное, что мы проверяем, — транзакции, блокировки и ограничения БД."""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://shop:shop@localhost:5433/shop_test")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("PSP_WEBHOOK_SECRET", "test-secret")
# Брокеры в тестах не нужны: проверяем то, что попадает в outbox, и обработчики воркеров
os.environ.setdefault("RABBITMQ_URL", "amqp://guest:guest@127.0.0.1:1/")
os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:1")

import itertools  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.db import SessionLocal, engine  # noqa: E402
from app.core.security import create_access_token, hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, Category, Product, Stock, User  # noqa: E402
from app.services import psp_client  # noqa: E402
from app.services.auth import login_limiter  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def database():
    """Чистая схема один раз на прогон — через настоящие миграции (заодно проверяем их)."""
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"))
        conn.execute(text("DROP SCHEMA IF EXISTS analytics CASCADE; DROP SCHEMA IF EXISTS audit CASCADE;"))
    command.upgrade(Config("alembic.ini"), "head")
    yield


@pytest.fixture(autouse=True)
def clean_tables():
    tables = ", ".join(f'{t.schema + "." if t.schema else ""}"{t.name}"' for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    login_limiter.reset()
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def client():
    return TestClient(app)


_counter = itertools.count(1)


@pytest.fixture
def make_user(db):
    def _make(role: str = "customer", email: str | None = None) -> tuple[User, dict]:
        user = User(
            email=email or f"user{next(_counter)}@example.com",
            password_hash=hash_password("Secret123"),
            role=role,
        )
        db.add(user)
        db.commit()
        headers = {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}
        return user, headers

    return _make


@pytest.fixture
def buyer(make_user):
    return make_user()


@pytest.fixture
def admin(make_user):
    return make_user(role="admin")


@pytest.fixture
def make_product(db):
    category = {}

    def _make(price_kopecks: int = 100_000, quantity: int = 5, reserved: int = 0, active: bool = True) -> Product:
        if "c" not in category:
            category["c"] = Category(name="Тест")
            db.add(category["c"])
            db.flush()
        n = next(_counter)
        product = Product(
            category_id=category["c"].id,
            sku=f"SKU-{n}",
            name=f"Товар {n}",
            price_kopecks=price_kopecks,
            is_active=active,
        )
        product.stock = Stock(quantity=quantity, reserved=reserved)
        db.add(product)
        db.commit()
        return product

    return _make


class FakePSP:
    """Подменяет HTTP-вызовы к PSP в тестах."""

    def __init__(self):
        self.available = True
        self.payments: list[dict] = []
        self.refunds: list[dict] = []

    def create_payment(self, order_id, amount_kopecks, idempotency_key):
        if not self.available:
            from app.core.errors import AppError

            raise AppError(503, "PSP_UNAVAILABLE", "PSP недоступен")
        payment = {
            "payment_id": f"pay_{len(self.payments) + 1}",
            "payment_url": "http://psp/pay",
            "status": "pending",
            "order_id": order_id,
            "amount_kopecks": amount_kopecks,
        }
        self.payments.append(payment)
        return payment

    def create_refund(self, provider_payment_id, amount_kopecks, idempotency_key):
        if not self.available:
            raise psp_client.PSPError("down")
        refund = {"refund_id": f"ref_{len(self.refunds) + 1}", "status": "pending", "payment_id": provider_payment_id}
        self.refunds.append(refund)
        return refund


@pytest.fixture
def psp(monkeypatch):
    fake = FakePSP()
    monkeypatch.setattr(psp_client, "create_payment", fake.create_payment)
    monkeypatch.setattr(psp_client, "create_refund", fake.create_refund)
    return fake
