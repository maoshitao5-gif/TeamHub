"""独立网页会话：同源防护、Cookie 轮换、退出及停用即时失效。"""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.web_auth import COOKIE_NAME
from app.core.security import decode_access_token, hash_password
from app.database import SessionLocal
from app.models import User
from main import app

ORIGIN = "https://testserver"
HEADERS = {"Origin": ORIGIN, "X-TeamHub-Web": "1"}


@pytest.fixture
def account():
    app.dependency_overrides.clear()
    with SessionLocal() as db:
        user = User(email=f"web-{uuid4().hex}@example.com", display_name="网页成员",
                    hashed_password=hash_password("web-test-password"))
        db.add(user)
        db.commit()
        result = {"id": user.id, "email": user.email, "password": "web-test-password"}
    yield result
    app.dependency_overrides.clear()


def sign_in(client, account):
    return client.post("/api/web-auth/login", json={
        "email": account["email"], "password": account["password"],
    }, headers=HEADERS)


def test_cookie_rotation_and_logout_revoke_access(account):
    with TestClient(app, base_url=ORIGIN) as client:
        response = sign_in(client, account)
        assert response.status_code == 200, response.text
        assert "refresh_token" not in response.json()
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
        assert "Path=/api/web-auth" in cookie
        first_cookie = client.cookies.get(COOKIE_NAME)
        token = response.json()["access_token"]
        assert decode_access_token(token)["sid"].startswith("web:")
        auth = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/users/me", headers=auth).status_code == 200
        for _ in range(2):
            assert client.post("/api/web-auth/refresh", headers=HEADERS).status_code == 200
        assert client.cookies.get(COOKIE_NAME) != first_cookie
        assert client.get("/api/users/me", headers=auth).status_code == 200
        assert client.post("/api/web-auth/logout", headers=HEADERS).status_code == 204
        assert client.cookies.get(COOKIE_NAME) is None
        assert client.get("/api/users/me", headers=auth).status_code == 401
        assert client.post("/api/web-auth/refresh", headers=HEADERS).status_code == 401


@pytest.mark.parametrize("headers", [
    {}, {"Origin": ORIGIN}, {"X-TeamHub-Web": "1"},
    {"Origin": "https://attacker.example", "X-TeamHub-Web": "1"},
    {"Origin": "null", "X-TeamHub-Web": "1"},
    {**HEADERS, "Sec-Fetch-Site": "cross-site"},
])
def test_cookie_routes_reject_cross_origin_and_missing_csrf_headers(account, headers):
    with TestClient(app, base_url=ORIGIN) as client:
        body = {"email": account["email"], "password": account["password"]}
        assert client.post("/api/web-auth/login", json=body, headers=headers).status_code == 403
        assert sign_in(client, account).status_code == 200
        for action in ("refresh", "logout"):
            assert client.post(f"/api/web-auth/{action}", headers=headers).status_code == 403
        assert client.post("/api/web-auth/refresh", headers=HEADERS).status_code == 200


def test_disabled_account_loses_access_and_refresh(account):
    with TestClient(app, base_url=ORIGIN) as client:
        token = sign_in(client, account).json()["access_token"]
        with SessionLocal() as db:
            db.get(User, account["id"]).is_active = False
            db.commit()
        assert client.get("/api/users/me", headers={"Authorization": f"Bearer {token}"}).status_code in (401, 403)
        assert client.post("/api/web-auth/refresh", headers=HEADERS).status_code == 401


def test_parallel_refresh_consumes_cookie_once(account):
    with TestClient(app, base_url=ORIGIN) as client:
        assert sign_in(client, account).status_code == 200
        raw = client.cookies.get(COOKIE_NAME)

    def rotate():
        with TestClient(app, base_url=ORIGIN) as other:
            return other.post("/api/web-auth/refresh", headers={
                **HEADERS, "Cookie": f"{COOKIE_NAME}={raw}",
            }).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: rotate(), range(2)))
    assert sorted(results) == [200, 401]


def test_http_only_permitted_for_loopback(account):
    body = {"email": account["email"], "password": account["password"]}
    for origin, expected in (("http://cloud.internal", 400), ("http://127.0.0.1", 200)):
        with TestClient(app, base_url=origin) as client:
            response = client.post("/api/web-auth/login", json=body, headers={"Origin": origin, "X-TeamHub-Web": "1"})
            assert response.status_code == expected
            if expected == 200:
                assert "Secure" not in response.headers["set-cookie"]


def test_login_replaces_previous_web_session(account):
    with TestClient(app, base_url=ORIGIN) as client:
        first = sign_in(client, account).json()["access_token"]
        assert sign_in(client, account).status_code == 200
        assert client.get("/api/users/me", headers={"Authorization": f"Bearer {first}"}).status_code == 401


def test_web_refresh_cannot_be_exchanged_for_unbound_desktop_token(account):
    with TestClient(app, base_url=ORIGIN) as client:
        assert sign_in(client, account).status_code == 200
        raw = client.cookies.get(COOKIE_NAME)
        legacy = client.post("/api/auth/refresh", json={"refresh_token": raw})
        assert legacy.status_code == 401
        assert "access_token" not in legacy.json()
        # 被拒绝的跨通道调用不能消费合法网页会话。
        assert client.post("/api/web-auth/refresh", headers=HEADERS).status_code == 200
