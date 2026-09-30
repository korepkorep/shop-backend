"""US-03..US-06: каталог и корзина."""

from app.models import Category, Product, Stock
from tests.helpers import API, add_to_cart


def test_catalog_filters_sort_and_pagination(client, db):
    root = Category(name="Гаджеты")
    db.add(root)
    db.flush()
    child = Category(name="Смартфоны", parent_id=root.id)
    other = Category(name="Мебель")
    db.add_all([child, other])
    db.flush()
    for sku, name, cat, price, active in [
        ("A", "Смартфон Альфа", child, 30_000_00, True),
        ("B", "Смартфон Бета", child, 10_000_00, True),
        ("C", "Часы", root, 20_000_00, True),
        ("D", "Стул", other, 5_000_00, True),
        ("E", "Снятый смартфон", child, 1_000_00, False),
    ]:
        p = Product(sku=sku, name=name, category_id=cat.id, price_kopecks=price, is_active=active)
        p.stock = Stock(quantity=3, reserved=1)
        db.add(p)
    db.commit()

    # Категория вместе с подкатегориями, неактивный товар скрыт
    r = client.get(f"{API}/products", params={"category_id": root.id, "sort": "price_asc"})
    assert [p["sku"] for p in r.json()["items"]] == ["B", "C", "A"]
    assert r.json()["items"][0]["available"] == 2

    r = client.get(f"{API}/products", params={"q": "смартфон"})
    assert {p["sku"] for p in r.json()["items"]} == {"A", "B"}

    r = client.get(f"{API}/products", params={"price_min": 10_000_00, "price_max": 20_000_00})
    assert {p["sku"] for p in r.json()["items"]} == {"B", "C"}

    r = client.get(f"{API}/products", params={"limit": 2, "offset": 2, "sort": "price_desc"})
    assert r.json()["total"] == 4
    assert [p["sku"] for p in r.json()["items"]] == ["B", "D"]


def test_inactive_product_card_is_404(client, make_product):
    product = make_product(active=False)
    assert client.get(f"{API}/products/{product.id}").status_code == 404


def test_cart_add_and_merge_quantities(client, buyer, make_product):
    _, headers = buyer
    product = make_product(price_kopecks=1_500_00, quantity=20)
    add_to_cart(client, headers, product.id, 2)
    r = add_to_cart(client, headers, product.id, 3)
    assert r.status_code == 201
    assert r.json()["items"][0]["quantity"] == 5
    assert r.json()["total_kopecks"] == 7_500_00


def test_cart_limit_of_10(client, buyer, make_product):
    _, headers = buyer
    product = make_product(quantity=50)
    add_to_cart(client, headers, product.id, 8)
    r = add_to_cart(client, headers, product.id, 3)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "CART_QUANTITY_LIMIT"


def test_cart_rejects_inactive_and_out_of_stock(client, buyer, make_product):
    _, headers = buyer
    inactive = make_product(active=False)
    scarce = make_product(quantity=2)
    assert add_to_cart(client, headers, inactive.id).json()["error"]["code"] == "PRODUCT_INACTIVE"
    assert add_to_cart(client, headers, scarce.id, 3).json()["error"]["code"] == "OUT_OF_STOCK"


def test_cart_marks_unavailable_items_but_keeps_them(client, db, buyer, make_product):
    _, headers = buyer
    product = make_product(quantity=5)
    add_to_cart(client, headers, product.id, 4)
    db.get(Stock, product.id).quantity = 2
    db.commit()
    cart = client.get(f"{API}/cart", headers=headers).json()
    assert cart["items"][0]["is_available"] is False
    assert cart["has_unavailable_items"] is True


def test_cart_delete_is_idempotent(client, buyer, make_product):
    _, headers = buyer
    product = make_product()
    add_to_cart(client, headers, product.id)
    assert client.delete(f"{API}/cart/items/{product.id}", headers=headers).status_code == 204
    assert client.delete(f"{API}/cart/items/{product.id}", headers=headers).status_code == 204
    assert client.get(f"{API}/cart", headers=headers).json()["items"] == []
