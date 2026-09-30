"""US-01, US-02: регистрация, вход, токены, роли."""

from app.core.security import create_refresh_token
from tests.helpers import API


def test_register_and_login(client):
    r = client.post(f"{API}/auth/register", json={"email": "Anna@Example.com", "password": "Secret123"})
    assert r.status_code == 201
    assert r.json()["email"] == "anna@example.com"
    assert r.json()["role"] == "customer"

    r = client.post(f"{API}/auth/login", json={"email": "anna@example.com", "password": "Secret123"})
    assert r.status_code == 200
    token = r.json()["access_token"]
    me = client.get(f"{API}/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["email"] == "anna@example.com"


def test_email_is_case_insensitive_unique(client):
    client.post(f"{API}/auth/register", json={"email": "anna@example.com", "password": "Secret123"})
    r = client.post(f"{API}/auth/register", json={"email": "ANNA@example.com", "password": "Secret123"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "EMAIL_TAKEN"


def test_weak_password_rejected(client):
    r = client.post(f"{API}/auth/register", json={"email": "a@example.com", "password": "onlyletters"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_wrong_password_and_unknown_email_look_the_same(client, buyer):
    user, _ = buyer
    wrong_password = client.post(f"{API}/auth/login", json={"email": user.email, "password": "Wrong1234"})
    unknown_email = client.post(f"{API}/auth/login", json={"email": "nobody@example.com", "password": "Wrong1234"})
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


def test_login_rate_limit(client, buyer):
    user, _ = buyer
    for _ in range(5):
        client.post(f"{API}/auth/login", json={"email": user.email, "password": "Wrong1234"})
    r = client.post(f"{API}/auth/login", json={"email": user.email, "password": "Secret123"})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "TOO_MANY_ATTEMPTS"


def test_refresh_token(client, buyer):
    user, _ = buyer
    r = client.post(f"{API}/auth/refresh", json={"refresh_token": create_refresh_token(user.id, user.role)})
    assert r.status_code == 200
    assert r.json()["access_token"]


def test_refresh_token_is_not_access_token(client, buyer):
    user, _ = buyer
    token = create_refresh_token(user.id, user.role)
    r = client.get(f"{API}/users/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_no_token_and_bad_token(client):
    assert client.get(f"{API}/users/me").status_code == 401
    assert client.get(f"{API}/users/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_customer_cannot_use_admin_api(client, buyer):
    _, headers = buyer
    r = client.get(f"{API}/admin/orders", headers=headers)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"
