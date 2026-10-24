"""桌面真实认证接口：连续续期、原子轮换及网页会话隔离。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from app.api import auth
from app.api.web_auth import COOKIE_NAME
from app.core.security import decode_access_token, hash_password, hash_refresh_token
from app.database import SessionLocal, engine
from app.models import RefreshToken, User
from main import app


@pytest.fixture
def desktop_session():
    # conftest 将数据库设为独立临时 SQLite，账号也逐例隔离。
    app.dependency_overrides.clear()
    credentials = {"email": f"desktop-refresh-{uuid4().hex}@example.com",
                   "password": "desktop-refresh-password"}
    with SessionLocal() as db:
        db.add(User(email=credentials["email"], display_name="桌面续期测试",
                    hashed_password=hash_password(credentials["password"])))
        db.commit()
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/auth/login", json=credentials)
        assert response.status_code == 200, response.text
        yield client, response.json(), credentials
    app.dependency_overrides.clear()


def _refresh(client, raw, **extra):
    return client.post("/api/auth/refresh", json={"refresh_token": raw, **extra})


def _record(raw):
    with SessionLocal() as db:
        return db.query(RefreshToken).filter_by(token_hash=hash_refresh_token(raw)).one()


def test_real_login_supports_three_successive_refreshes(desktop_session):
    client, registered, credentials = desktop_session
    response = client.post("/api/auth/login", json=credentials)
    assert response.status_code == 200
    current = response.json()["refresh_token"]
    seen = {current}
    for _ in range(3):
        response = _refresh(client, current)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["token_type"] == "bearer"
        assert data.get("refresh_token"), "真实服务必须返回轮换后的刷新令牌"
        assert data["refresh_token"] not in seen
        assert decode_access_token(data["access_token"])["sub"] == registered["user"]["id"]
        assert client.get("/api/users/me", headers={
            "Authorization": f"Bearer {data['access_token']}",
        }).status_code == 200
        assert _refresh(client, current).status_code == 401
        assert _record(current).revoked_at is not None
        current = data["refresh_token"]
        seen.add(current)
        assert _record(current).revoked_at is None
        assert _record(current).token_hash != current
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("device_id", [None, "desktop-device-one"])
def test_rotation_preserves_original_device_not_request_override(desktop_session, device_id):
    client, session, _ = desktop_session
    old = session["refresh_token"]
    with SessionLocal() as db:
        db.query(RefreshToken).filter_by(token_hash=hash_refresh_token(old)).update({"device_id": device_id})
        db.commit()
    response = _refresh(client, old, device_id="web:forged-device")
    assert response.status_code == 200, response.text
    assert response.json().get("refresh_token")
    assert _record(response.json()["refresh_token"]).device_id == device_id


@pytest.mark.parametrize("failure", ["signing", "duplicate_storage"])
def test_issuance_failure_rolls_back_old_token_consumption(desktop_session, monkeypatch, failure):
    client, session, _ = desktop_session
    old = session["refresh_token"]
    with monkeypatch.context() as patch:
        if failure == "signing":
            def fail_signing(*args, **kwargs):
                raise RuntimeError("模拟访问令牌签发失败")
            patch.setattr(auth, "create_access_token", fail_signing)
        else:
            # 唯一索引冲突模拟新令牌落库失败；旧令牌的撤销必须同时回滚。
            patch.setattr(auth, "create_refresh_token_value", lambda: old)
        assert _refresh(client, old).status_code == 500
    assert _record(old).revoked_at is None
    response = _refresh(client, old)
    assert response.status_code == 200, response.text
    assert response.json().get("refresh_token")


def test_concurrent_refresh_consumes_old_token_exactly_once(desktop_session):
    _, session, _ = desktop_session
    old = session["refresh_token"]
    token_hash = hash_refresh_token(old)
    barrier = Barrier(2)

    def synchronize_read(conn, cursor, statement, parameters, context, executemany):
        # 让两个请求均读到尚未撤销的旧令牌，再开始竞争消费，避免测试只靠调度碰巧串行。
        if statement.lstrip().upper().startswith("SELECT") and "refresh_tokens.token_hash =" in statement and token_hash in parameters:
            barrier.wait(timeout=10)

    def rotate(_):
        with TestClient(app, raise_server_exceptions=False) as client:
            response = _refresh(client, old)
            return response.status_code, response.json()

    event.listen(engine, "after_cursor_execute", synchronize_read)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(rotate, range(2)))
    finally:
        event.remove(engine, "after_cursor_execute", synchronize_read)
    assert sorted(code for code, _ in responses) == [200, 401]
    renewed = next(data for code, data in responses if code == 200)
    assert renewed.get("refresh_token")
    with SessionLocal() as db:
        active = db.query(RefreshToken).filter_by(user_id=session["user"]["id"], revoked_at=None).all()
        assert len(active) == 1
        assert active[0].token_hash == hash_refresh_token(renewed["refresh_token"])
    with TestClient(app) as client:
        assert _refresh(client, renewed["refresh_token"]).status_code == 200


@pytest.mark.parametrize("state", ["expired", "revoked", "disabled", "unknown"])
def test_invalid_refresh_issues_no_new_tokens(desktop_session, state):
    client, session, _ = desktop_session
    old = session["refresh_token"]
    with SessionLocal() as db:
        token = db.query(RefreshToken).filter_by(token_hash=hash_refresh_token(old)).one()
        if state == "expired":
            token.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        elif state == "revoked":
            token.revoked_at = datetime.now(timezone.utc)
        elif state == "disabled":
            db.get(User, session["user"]["id"]).is_active = False
        db.commit()
        before = db.query(RefreshToken).filter_by(user_id=session["user"]["id"]).count()
    response = _refresh(client, "not-a-token" if state == "unknown" else old)
    assert response.status_code == 401
    assert "access_token" not in response.json()
    assert "refresh_token" not in response.json()
    with SessionLocal() as db:
        assert db.query(RefreshToken).filter_by(user_id=session["user"]["id"]).count() == before


def test_account_disabled_between_read_and_rotation_is_rejected(desktop_session):
    client, session, _ = desktop_session
    user_id = session["user"]["id"]

    def disable_after_read(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and "FROM users" in statement and user_id in parameters:
            with SessionLocal() as db:
                db.query(User).filter_by(id=user_id).update({"is_active": False})
                db.commit()

    event.listen(engine, "after_cursor_execute", disable_after_read)
    try:
        response = _refresh(client, session["refresh_token"])
    finally:
        event.remove(engine, "after_cursor_execute", disable_after_read)
    assert response.status_code == 401
    assert _record(session["refresh_token"]).revoked_at is None
    with SessionLocal() as db:
        assert db.query(RefreshToken).filter_by(user_id=user_id).count() == 1


def test_desktop_rotation_cannot_consume_web_cookie_and_reverse(desktop_session):
    _, desktop, credentials = desktop_session
    origin = "https://testserver"
    headers = {"Origin": origin, "X-TeamHub-Web": "1"}
    with TestClient(app, base_url=origin) as client:
        assert client.post("/api/web-auth/login", json=credentials, headers=headers).status_code == 200
        web_refresh = client.cookies.get(COOKIE_NAME)
        assert _refresh(client, web_refresh).status_code == 401
        assert _record(web_refresh).revoked_at is None
        assert client.post("/api/web-auth/refresh", headers=headers).status_code == 200
        # 网页刷新也不能把桌面令牌转换成 Cookie 会话。
        response = client.post("/api/web-auth/refresh", headers={
            **headers, "Cookie": f"{COOKIE_NAME}={desktop['refresh_token']}",
        })
        assert response.status_code == 401
        assert _record(desktop["refresh_token"]).revoked_at is None
        assert _refresh(client, desktop["refresh_token"]).status_code == 200
