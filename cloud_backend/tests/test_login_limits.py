"""两种登录入口共用限流；临时时钟和合成账号，不等待真实窗口。"""
from concurrent.futures import ThreadPoolExecutor
import threading
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app.core import login_limits
from app.database import SessionLocal
from app.models import User
from main import app


def test_account_limit_is_shared_across_ips_and_expires():
    now = [100.0]
    limiter = login_limits.LoginLimiter(account_limit=2, clock=lambda: now[0])
    limiter.check("synthetic@example.com", "192.0.2.1")
    limiter.check("synthetic@example.com", "192.0.2.2")
    with pytest.raises(HTTPException) as blocked:
        limiter.check("synthetic@example.com", "192.0.2.3")
    assert blocked.value.status_code == 429
    assert blocked.value.headers["Retry-After"] == "60"
    now[0] += 60
    limiter.check("synthetic@example.com", "192.0.2.3")


def test_ip_limit_is_shared_across_accounts_and_bucket_memory_is_bounded():
    limiter = login_limits.LoginLimiter(ip_limit=2)
    limiter.check("one@example.com", "192.0.2.1")
    limiter.check("two@example.com", "192.0.2.1")
    with pytest.raises(HTTPException) as blocked:
        limiter.check("three@example.com", "192.0.2.1")
    assert blocked.value.status_code == 429
    bounded = login_limits.LoginLimiter(max_keys=4)
    for index in range(30):
        bounded.check(f"synthetic-{index}@example.com", f"192.0.2.{index}")
    assert len(bounded._buckets) <= 4


def test_parallel_attempts_cannot_exceed_account_limit():
    limiter = login_limits.LoginLimiter(account_limit=3)

    def attempt(_):
        try:
            limiter.check("synthetic@example.com", "192.0.2.1")
            return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=10) as executor:
        statuses = list(executor.map(attempt, range(10)))
    assert statuses.count(200) == 3 and statuses.count(429) == 7


@pytest.mark.parametrize("existing", [True, False])
def test_desktop_and_web_login_share_limits_without_account_disclosure(monkeypatch, existing):
    from app.api import auth
    app.dependency_overrides.clear()
    limiter = login_limits.LoginLimiter(account_limit=2)
    monkeypatch.setattr(login_limits, "login_limiter", limiter)
    email = f"login-limit-{uuid4().hex}@example.com"
    if existing:
        with SessionLocal() as db:
            db.add(User(email=email, hashed_password="synthetic", display_name="Synthetic"))
            db.commit()
    monkeypatch.setattr(auth, "verify_password", lambda *args: False)
    with TestClient(app, base_url="https://testserver") as client:
        body = {"email": email, "password": "incorrect"}
        headers = {"Origin": "https://testserver", "X-TeamHub-Web": "1"}
        first = client.post("/api/auth/login", json=body)
        second = client.post("/api/web-auth/login", json=body, headers=headers)
        assert first.status_code == second.status_code == 401
        assert first.json() == second.json() == {"detail": "邮箱或密码错误"}
        for route in ("/api/auth/login", "/api/web-auth/login"):
            result = client.post(route, json=body, headers=headers)
            assert result.status_code == 429
            assert result.headers["Retry-After"].isdigit()
            assert result.json()["detail"] == "登录尝试过于频繁，请稍后重试"


def test_password_verification_does_not_block_other_requests(monkeypatch):
    from app.api import auth
    app.dependency_overrides.clear()
    monkeypatch.setattr(login_limits, "login_limiter", login_limits.LoginLimiter())
    email = f"login-thread-{uuid4().hex}@example.com"
    with SessionLocal() as db:
        db.add(User(email=email, hashed_password="synthetic", display_name="Synthetic"))
        db.commit()
    entered, release = threading.Event(), threading.Event()

    def slow_password_check(*args):
        entered.set()
        release.wait(timeout=5)
        return False

    monkeypatch.setattr(auth, "verify_password", slow_password_check)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as executor:
        login = executor.submit(client.post, "/api/auth/login", json={"email": email, "password": "incorrect"})
        try:
            assert entered.wait(timeout=2)
            health = executor.submit(client.get, "/api/health")
            assert health.result(timeout=2).status_code == 200
        finally:
            release.set()
        assert login.result(timeout=2).status_code == 401
