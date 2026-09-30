"""Тестовые данные: пользователи, категории, товары, остатки.

Запуск: python -m scripts.seed  (в Docker выполняется автоматически при старте API).
Скрипт идемпотентен: если данные уже есть, ничего не делает.
"""

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models import Category, Product, Stock, User

USERS = [
    ("admin@example.com", "Admin12345", "admin"),
    ("buyer@example.com", "Buyer12345", "customer"),
    ("buyer2@example.com", "Buyer12345", "customer"),
    # Письма на этот адрес «не отправляются» — для демо ретраев и parking-очереди
    ("fail-buyer@example.com", "Buyer12345", "customer"),
]

# (категория, родитель)
CATEGORIES = [
    ("Смартфоны и гаджеты", None),
    ("Смартфоны", "Смартфоны и гаджеты"),
    ("Наушники", "Смартфоны и гаджеты"),
    ("Умные часы", "Смартфоны и гаджеты"),
    ("Компьютеры", None),
    ("Ноутбуки", "Компьютеры"),
    ("Мониторы", "Компьютеры"),
    ("Аксессуары", "Компьютеры"),
]

# (sku, название, категория, цена в рублях, остаток, описание)
PRODUCTS = [
    ("PH-VOLT-X1", "Смартфон Volt X1 128 ГБ", "Смартфоны", 34990, 25, "6,1 дюйма, OLED, 48 Мп"),
    ("PH-VOLT-X1P", "Смартфон Volt X1 Pro 256 ГБ", "Смартфоны", 54990, 10, "6,7 дюйма, OLED, 3 камеры"),
    ("PH-NOVA-5", "Смартфон Nova 5 64 ГБ", "Смартфоны", 15990, 40, "Бюджетная модель, 5000 мА·ч"),
    ("PH-LAST-1", "Смартфон Limited Edition", "Смартфоны", 99990, 1, "Последний экземпляр — для демо гонки"),
    ("HP-BEAT-AIR", "Наушники Beat Air", "Наушники", 7990, 60, "Беспроводные, шумоподавление"),
    ("HP-BEAT-PRO", "Наушники Beat Pro", "Наушники", 19990, 15, "Полноразмерные, 40 ч работы"),
    ("WT-PULSE-2", "Умные часы Pulse 2", "Умные часы", 12990, 30, "Пульсометр, GPS, NFC"),
    ("NB-AIRBOOK-13", "Ноутбук AirBook 13", "Ноутбуки", 89990, 8, "13,3 дюйма, 16 ГБ, SSD 512 ГБ"),
    ("NB-WORK-15", "Ноутбук WorkStation 15", "Ноутбуки", 129990, 5, "15,6 дюйма, 32 ГБ, SSD 1 ТБ"),
    ("MN-VIEW-27", "Монитор View 27 4K", "Мониторы", 32990, 12, "27 дюймов, IPS, 4K"),
    ("AC-MOUSE-1", "Мышь беспроводная M1", "Аксессуары", 1490, 200, "Тихие клавиши"),
    ("AC-KEYB-1", "Клавиатура механическая K1", "Аксессуары", 5990, 50, "Русская раскладка"),
    ("AC-HUB-7", "USB-C хаб 7 в 1", "Аксессуары", 3490, 0, "Нет в наличии — для демо OUT_OF_STOCK"),
]


def seed() -> None:
    with SessionLocal() as db:
        if db.scalar(select(User.id).limit(1)):
            print("Данные уже есть, сид пропущен")
            return

        for email, password, role in USERS:
            db.add(User(email=email, password_hash=hash_password(password), role=role))

        categories: dict[str, Category] = {}
        for name, parent in CATEGORIES:
            category = Category(name=name, parent_id=categories[parent].id if parent else None)
            db.add(category)
            db.flush()
            categories[name] = category

        for sku, name, category, price_rub, quantity, description in PRODUCTS:
            product = Product(
                sku=sku,
                name=name,
                category_id=categories[category].id,
                price_kopecks=price_rub * 100,
                description=description,
                is_active=True,
            )
            product.stock = Stock(quantity=quantity, reserved=0)
            db.add(product)

        db.commit()
        print(f"Создано: {len(USERS)} пользователя, {len(CATEGORIES)} категорий, {len(PRODUCTS)} товаров")


if __name__ == "__main__":
    seed()
